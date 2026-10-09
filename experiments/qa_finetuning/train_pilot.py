"""Standalone GPU pilot: complete-chain closed-book SFT and paired evaluation.

Each stage runs in its own process. Training resumes only at atomic optimizer
checkpoints, restoring optimizer, scheduler and RNG, never mid-accumulation.
"""
import argparse
from collections import Counter
import fcntl
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time

SEED = 42
MAX_SEQUENCE = 32768
MAX_NEW_TOKENS = 4096
GROUP_SIZE = 8
SAVE_EVERY = 10
LR = 2e-5
RANK = 32
ALPHA = 64
LOSS_CHUNK = 128
TARGETS = ['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj']


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def status(run, stage, state, **extra):
    value = {'stage': stage, 'state': state, 'pid': os.getpid(),
             'updated_at': time.time(), **extra}
    save(run / f'{stage}_status.json', value)
    print(json.dumps(value, ensure_ascii=False), flush=True)


def messages(row, open_book=False, include_answer=False):
    user = row['question']
    if open_book:
        user = '以下是本题适用的文档资料：\n\n' + row['context'] + '\n\n问题：\n' + user
    result = [{'role': 'system', 'content': row['system']},
              {'role': 'user', 'content': user}]
    if include_answer:
        result.append({'role': 'assistant', 'content': row['answer']})
    return result


def encode(tokenizer, chat, generation=False):
    return tokenizer.apply_chat_template(chat, tokenize=True,
                                        add_generation_prompt=generation,
                                        enable_thinking=False)


def tokenize_training(tokenizer, run):
    result = []
    for row in rows(run / 'data/train.jsonl'):
        prefix = encode(tokenizer, messages(row), True)
        full = encode(tokenizer, messages(row, include_answer=True))
        assert full[:len(prefix)] == prefix, f"Template prefix mismatch: {row['id']}"
        assert len(prefix) < len(full) <= MAX_SEQUENCE, f"Complete sample too long: {row['id']}"
        result.append({'id': row['id'], 'tokens': full, 'prompt_length': len(prefix),
                       'full_tokens': len(full), 'target_tokens': len(full) - len(prefix)})
    return result


def validate(run):
    frozen = json.loads((run / 'data/frozen.json').read_text())
    for name, expected in frozen['files'].items():
        assert sha(run / 'data' / name) == expected, f'Dataset changed: {name}'
    train = rows(run / 'data/train.jsonl')
    dev = rows(run / 'data/development.jsonl')
    assert len(train) == frozen['train'] and len(dev) == frozen['development_questions'] + frozen['trained_question_fit_diagnostics']
    assert len({r['id'] for r in train}) == len(train)
    assert len({r['id'] for r in dev}) == len(dev)
    held = [r for r in dev if r['evaluation_role'] == 'development_group_held_out_not_final_test']
    assert not ({r['chain_id'] for r in train} & {r['chain_id'] for r in held})
    assert {r['id'] for r in dev if r['evaluation_role'] == 'trained_question_fit_diagnostic'} <= {r['id'] for r in train}
    return frozen


def make_config(run, model):
    frozen = validate(run)
    return {'model': frozen['model'], 'revision': frozen['revision'], 'model_path': str(model),
            'train_sha256': frozen['files']['train.jsonl'],
            'development_sha256': frozen['files']['development.jsonl'],
            'runner_sha256': sha(Path(__file__)), 'seed': SEED,
            'epochs': 1, 'batch_size': 1, 'gradient_accumulation': GROUP_SIZE,
            'learning_rate': LR, 'warmup_steps': 3, 'weight_decay': 0.01,
            'lora_rank': RANK, 'lora_alpha': ALPHA, 'lora_dropout': 0.05,
            'lora_target_modules': TARGETS, 'precision': 'bf16_no_quantization',
            'attention': 'sdpa', 'gradient_checkpointing': 'non_reentrant',
            'loss': 'assistant_and_template_terminator_only',
            'loss_reduction': 'mean_tokens_per_sample_then_mean_samples_per_group',
            'loss_projection_chunk': LOSS_CHUNK, 'input_truncation': False,
            'sample_packing': False, 'max_sequence_tokens': MAX_SEQUENCE,
            'enable_thinking': False, 'training_input': 'system_and_question_without_document',
            'checkpoint_every_optimizer_steps': SAVE_EVERY,
            'evaluation': {'views': ['closed', 'open'], 'max_new_tokens': MAX_NEW_TOKENS,
                           'do_sample': True, 'temperature': 0.7, 'top_p': 0.8, 'top_k': 20,
                           'repetition_penalty': 1.0, 'seed_per_question': '42 + row_index'},
            'expert_scored': False,
            'versions': {p: importlib.metadata.version(p) for p in
                         ['torch', 'transformers', 'peft', 'huggingface-hub', 'accelerate']}}


def load(model_path, training=False, adapter=None):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(model_path, local_files_only=True,
        dtype=torch.bfloat16, device_map={'': 0}, attn_implementation='sdpa')
    if training:
        from peft import LoraConfig, PeftModel, get_peft_model
        if adapter is None:
            model = get_peft_model(model, LoraConfig(r=RANK, lora_alpha=ALPHA,
                lora_dropout=0.05, target_modules=TARGETS, bias='none', task_type='CAUSAL_LM'))
        else:
            model = PeftModel.from_pretrained(model, adapter, is_trainable=True)
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
        model.enable_input_require_grads()
        model.config.use_cache = False
        model.train()
    else:
        if adapter is not None:
            from peft import PeftModel
            model = PeftModel.from_pretrained(model, adapter).merge_and_unload()
        model.eval()
    return tokenizer, model


def completion_loss(model, ids, prompt_length, chunk_size=LOSS_CHUNK):
    """Equivalent to masked causal CE; checkpoint bounded answer logits.

    Full-vocabulary projection of every long answer at once wastes gigabytes.
    Recompute only projection/CE chunks in backward; preserve the whole chain.
    """
    import torch
    from torch.utils.checkpoint import checkpoint
    base = model.get_base_model() if hasattr(model, 'get_base_model') else model
    hidden = base.model(input_ids=ids, use_cache=False).last_hidden_state
    hidden = hidden[:, prompt_length - 1:-1, :]
    targets = ids[:, prompt_length:]
    assert hidden.shape[:2] == targets.shape and targets.numel() > 0
    head = base.get_output_embeddings()

    def project(part, labels):
        logits = head(part).float()
        return torch.nn.functional.cross_entropy(logits.reshape(-1, logits.shape[-1]),
                                                 labels.reshape(-1), reduction='sum')

    loss = hidden.new_zeros((), dtype=torch.float32)
    for start in range(0, targets.shape[1], chunk_size):
        loss = loss + checkpoint(project, hidden[:, start:start + chunk_size, :],
                                 targets[:, start:start + chunk_size], use_reentrant=False)
    return loss / targets.numel()


def verify_loss():
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import Qwen3Config, Qwen3ForCausalLM
    torch.manual_seed(SEED)
    base = Qwen3ForCausalLM(Qwen3Config(vocab_size=97, hidden_size=32,
        intermediate_size=64, num_hidden_layers=2, num_attention_heads=4,
        num_key_value_heads=2, head_dim=8, attention_dropout=0.0))
    model = get_peft_model(base, LoraConfig(r=4, lora_alpha=8, lora_dropout=0.0,
        target_modules=TARGETS, bias='none', task_type='CAUSAL_LM'))
    # Nonzero B makes both A and B gradients informative in the parity check.
    with torch.no_grad():
        for name, param in model.named_parameters():
            if 'lora_B' in name:
                param.normal_(std=0.01)
    ids = torch.randint(0, 97, (1, 19))
    results = []
    for prompt_length in [1, 7, 18]:
        model.zero_grad(set_to_none=True)
        labels = ids.clone()
        labels[:, :prompt_length] = -100
        expected = model(input_ids=ids, labels=labels, use_cache=False).loss
        expected.backward()
        gradients = {n: p.grad.clone() for n, p in model.named_parameters() if p.grad is not None}
        model.zero_grad(set_to_none=True)
        actual = completion_loss(model, ids, prompt_length, chunk_size=3)
        actual.backward()
        torch.testing.assert_close(actual, expected, atol=1e-6, rtol=1e-5)
        for name, param in model.named_parameters():
            if name in gradients:
                torch.testing.assert_close(param.grad, gradients[name], atol=1e-6, rtol=1e-4)
        results.append({'prompt_tokens': prompt_length, 'loss': actual.item()})
    return {'masked_loss_and_lora_gradient_parity': 'passed', 'cases': results}


def prepare(run, model_path):
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    prepared = tokenize_training(tokenizer, run)
    distributions = {}
    for key in ['full_tokens', 'target_tokens', 'prompt_length']:
        values = sorted(r[key] for r in prepared)
        distributions[key] = {'min': values[0], 'median': values[len(values) // 2],
                              'p95': values[int((len(values) - 1) * 0.95)],
                              'max': values[-1], 'sum': sum(values)}
    dev = []
    for row in rows(run / 'data/development.jsonl'):
        for view in ['closed', 'open']:
            tokens = encode(tokenizer, messages(row, view == 'open'), True)
            assert len(tokens) + MAX_NEW_TOKENS <= MAX_SEQUENCE, f"Eval context too long: {row['id']}/{view}"
            dev.append({'id': row['id'], 'view': view, 'input_tokens': len(tokens),
                        'evaluation_role': row['evaluation_role']})
    worst = {max(prepared, key=lambda r: r['full_tokens'])['id'],
             max(prepared, key=lambda r: r['target_tokens'])['id']}
    save(run / 'preparation.json', {'status': 'passed', 'train_rows': len(prepared),
         'distributions': distributions, 'profile_ids': sorted(worst),
         'evaluation_prompts': dev, 'input_truncation': False,
         'tokenizer_sha256': sha(model_path / 'tokenizer.json'),
         'chat_template_sha256': hashlib.sha256(tokenizer.chat_template.encode()).hexdigest(),
         **verify_loss()})
    print(json.dumps(distributions, indent=2), flush=True)


def profile(run, model_path):
    import torch
    from transformers import set_seed
    set_seed(SEED)
    tokenizer, model = load(model_path, training=True)
    data = tokenize_training(tokenizer, run)
    worst_ids = json.loads((run / 'preparation.json').read_text())['profile_ids']
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=LR, weight_decay=0.01)
    events = []
    for item in data:
        if item['id'] not in worst_ids:
            continue
        optimizer.zero_grad(set_to_none=True)
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        start = time.monotonic()
        ids = torch.tensor([item['tokens']], device='cuda')
        with torch.autocast('cuda', dtype=torch.bfloat16):
            loss = completion_loss(model, ids, item['prompt_length'])
        assert torch.isfinite(loss)
        loss.backward()
        assert all(p.grad is not None and torch.isfinite(p.grad).all() for p in params)
        norm = torch.nn.utils.clip_grad_norm_(params, 1.0)
        assert torch.isfinite(norm) and norm > 0
        optimizer.step()
        torch.cuda.synchronize()
        event = {'id': item['id'], 'full_tokens': item['full_tokens'],
                 'target_tokens': item['target_tokens'], 'loss': loss.item(),
                 'grad_norm': norm.item(), 'seconds': time.monotonic() - start,
                 'peak_allocated_mib': torch.cuda.max_memory_allocated() / 1024**2,
                 'peak_reserved_mib': torch.cuda.max_memory_reserved() / 1024**2}
        events.append(event)
        print(json.dumps(event), flush=True)
        del ids, loss
    save(run / 'profile.json', {'status': 'passed', 'disposable_optimizer_steps': len(events),
         'adapter_saved': False, 'trainable_parameters': sum(p.numel() for p in params),
         'precision': 'bf16_no_quantization', 'input_truncation': False, 'samples': events})


def evaluate(run, model_path, stage):
    import torch
    from transformers import set_seed
    folder = run / stage
    folder.mkdir(exist_ok=True)
    adapter = None
    if stage == 'after':
        adapter = json.loads((run / 'train/latest.json').read_text())['checkpoint']
        assert json.loads((run / 'train_status.json').read_text())['state'] == 'complete'
    tokenizer, model = load(model_path, adapter=adapter)
    data = rows(run / 'data/development.jsonl')
    journal = folder / 'responses.jsonl'
    previous = rows(journal) if journal.exists() else []
    done = {(r['id'], r['view']): r for r in previous}
    expected = {(r['id'], view) for r in data for view in ['closed', 'open']}
    assert len(done) == len(previous) and set(done) <= expected
    for index, row in enumerate(data):
        for view in ['closed', 'open']:
            if (row['id'], view) in done:
                continue
            tokens = encode(tokenizer, messages(row, view == 'open'), True)
            assert len(tokens) + MAX_NEW_TOKENS <= MAX_SEQUENCE
            ids = torch.tensor([tokens], device='cuda')
            set_seed(SEED + index)
            start = time.monotonic()
            with torch.inference_mode():
                output = model.generate(input_ids=ids, attention_mask=torch.ones_like(ids),
                    do_sample=True, temperature=0.7, top_p=0.8, top_k=20,
                    max_new_tokens=MAX_NEW_TOKENS, repetition_penalty=1.0,
                    pad_token_id=tokenizer.eos_token_id, use_cache=True)
            result = output[0, len(tokens):].tolist()
            eos = model.generation_config.eos_token_id
            eos = [eos] if isinstance(eos, int) else eos
            stopped = bool(result and result[-1] in eos)
            answer = tokenizer.decode(result, skip_special_tokens=True)
            event = {'id': row['id'], 'view': view, 'evaluation_role': row['evaluation_role'],
                     'family': row['family'], 'answer': answer,
                     'input_tokens': len(tokens), 'output_tokens': len(result),
                     'finish_reason': 'stop' if stopped else 'length',
                     'generation_status': ('ok' if answer.strip() else 'empty') if stopped else 'incomplete',
                     'seconds': time.monotonic() - start, 'seed': SEED + index}
            with journal.open('a') as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            done[row['id'], view] = event
            status(run, stage, 'running', responses=len(done), expected=len(expected),
                   latest={k: event[k] for k in ['id', 'view', 'output_tokens', 'generation_status', 'seconds']})
            del ids, output
    groups = {}
    for role in sorted({r['evaluation_role'] for r in data}):
        for view in ['closed', 'open']:
            group = [r for r in done.values() if r['evaluation_role'] == role and r['view'] == view]
            groups[role + '/' + view] = {'questions': len(group),
                'generation_status': dict(Counter(r['generation_status'] for r in group)),
                'total_seconds': sum(r['seconds'] for r in group),
                'output_tokens': sum(r['output_tokens'] for r in group)}
    save(folder / 'summary.json', {'responses': len(done), 'groups': groups,
         'answer_correctness_scored': False, 'adapter': adapter,
         'note': '完整生成不等于正确；留出链路与训练题拟合分别报告。'})
    status(run, stage, 'complete', responses=len(done), expected=len(expected),
           length_limited=sum(r['generation_status'] == 'incomplete' for r in done.values()))


def checkpoint(run, model, optimizer, scheduler, next_start, step, count, history):
    import numpy as np
    import torch
    folder = run / 'train'
    folder.mkdir(exist_ok=True)
    path = folder / f'checkpoint-{step:05d}'
    temporary = folder / f'.checkpoint-{step:05d}-{os.getpid()}.tmp'
    temporary.mkdir()
    model.save_pretrained(temporary)
    torch.save({'optimizer': optimizer.state_dict(), 'scheduler': scheduler.state_dict(),
                'torch_rng': torch.get_rng_state(), 'cuda_rng': torch.cuda.get_rng_state_all(),
                'python_rng': random.getstate(), 'numpy_rng': np.random.get_state()},
               temporary / 'training_state.pt')
    record = {'optimizer_step': step, 'sample_count': count, 'next_start': next_start,
              'history': history, 'config_sha256': sha(run / 'config.json')}
    save(temporary / 'state.json', record)
    temporary.rename(path)
    save(folder / 'latest.json', {'checkpoint': str(path), 'optimizer_step': step,
                                'sample_count': count, 'next_start': next_start})


def train(run, model_path):
    import numpy as np
    import torch
    from transformers import get_linear_schedule_with_warmup, set_seed
    set_seed(SEED)
    folder = run / 'train'
    folder.mkdir(exist_ok=True)
    latest = folder / 'latest.json'
    adapter = Path(json.loads(latest.read_text())['checkpoint']) if latest.exists() else None
    tokenizer, model = load(model_path, training=True, adapter=adapter)
    prepared = tokenize_training(tokenizer, run)
    order = list(range(len(prepared)))
    random.Random(SEED).shuffle(order)
    params = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(params, lr=LR, weight_decay=0.01)
    total_steps = (len(order) + GROUP_SIZE - 1) // GROUP_SIZE
    scheduler = get_linear_schedule_with_warmup(optimizer, num_warmup_steps=3,
                                               num_training_steps=total_steps)
    step, count, next_start, history = 0, 0, 0, []
    if adapter is not None:
        state = json.loads((adapter / 'state.json').read_text())
        assert state['config_sha256'] == sha(run / 'config.json'), 'Resume configuration changed'
        saved = torch.load(adapter / 'training_state.pt', map_location='cpu', weights_only=False)
        optimizer.load_state_dict(saved['optimizer'])
        scheduler.load_state_dict(saved['scheduler'])
        torch.set_rng_state(saved['torch_rng'])
        torch.cuda.set_rng_state_all(saved['cuda_rng'])
        random.setstate(saved['python_rng'])
        np.random.set_state(saved['numpy_rng'])
        step, count, next_start, history = (state[k] for k in ['optimizer_step', 'sample_count', 'next_start', 'history'])
        assert count == next_start and step == (next_start + GROUP_SIZE - 1) // GROUP_SIZE
        print(json.dumps({'resumed_checkpoint': str(adapter), 'next_start': next_start}), flush=True)
    started = time.monotonic()
    for start in range(next_start, len(order), GROUP_SIZE):
        group = order[start:start + GROUP_SIZE]
        optimizer.zero_grad(set_to_none=True)
        losses = []
        group_started = time.monotonic()
        for index in group:
            item = prepared[index]
            ids = torch.tensor([item['tokens']], device='cuda')
            with torch.autocast('cuda', dtype=torch.bfloat16):
                loss = completion_loss(model, ids, item['prompt_length'])
            assert torch.isfinite(loss), f"Nonfinite loss: {item['id']}"
            (loss / len(group)).backward()
            losses.append(loss.item())
            count += 1
            status(run, 'train', 'running', optimizer_step=step, total_steps=total_steps,
                   sample_count=count, expected_samples=len(order), latest_id=item['id'],
                   latest_sample_loss=losses[-1], seconds_this_invocation=time.monotonic() - started)
            del ids, loss
        norm = torch.nn.utils.clip_grad_norm_(params, 1.0)
        assert torch.isfinite(norm)
        optimizer.step()
        scheduler.step()
        step += 1
        next_start = start + len(group)
        history.append({'step': step, 'sample_count': count, 'mean_loss': sum(losses) / len(losses),
                        'grad_norm': norm.item(), 'next_learning_rate': scheduler.get_last_lr()[0],
                        'seconds': time.monotonic() - group_started})
        if step % SAVE_EVERY == 0 or next_start == len(order):
            checkpoint(run, model, optimizer, scheduler, next_start, step, count, history)
        save(folder / 'history.json', history)
        status(run, 'train', 'running', optimizer_step=step, total_steps=total_steps,
               sample_count=count, expected_samples=len(order), latest_group_loss=history[-1]['mean_loss'],
               seconds_this_invocation=time.monotonic() - started)
    status(run, 'train', 'complete', optimizer_step=step, total_steps=total_steps,
           sample_count=count, expected_samples=len(order), latest_checkpoint=json.loads(latest.read_text())['checkpoint'],
           seconds_this_invocation=time.monotonic() - started)


def compare(run):
    before = {(r['id'], r['view']): r for r in rows(run / 'baseline/responses.jsonl')}
    after = {(r['id'], r['view']): r for r in rows(run / 'after/responses.jsonl')}
    comparisons = []
    for row in rows(run / 'data/development.jsonl'):
        comparisons.append({**row, 'responses': {stage + '_' + view: data[row['id'], view]
                           for stage, data in [('base', before), ('sft', after)]
                           for view in ['closed', 'open']}})
    (run / 'comparison.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in comparisons))
    save(run / 'result.json', {'state': 'awaiting_answer_quality_review',
         'training': json.loads((run / 'train_status.json').read_text()),
         'baseline': json.loads((run / 'baseline/summary.json').read_text()),
         'after': json.loads((run / 'after/summary.json').read_text()),
         'comparison_rows': len(comparisons), 'expert_scored': False,
         'note': '先逐题检查条件、分支、顺序、禁止事项、恢复条件与杜撰，再决定蒸馏。'})


def pipeline(run, model_path):
    with (run / 'pipeline.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = make_config(run, model_path)
        existing = run / 'config.json'
        if existing.exists():
            assert json.loads(existing.read_text()) == config, 'Frozen run configuration changed'
        else:
            save(existing, config)
            (run / 'environment.txt').write_text(subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True))
        stage = 'download'
        try:
            status(run, 'job', 'running', active_stage=stage)
            # An independent systemd download may have been started while preparing code.
            # The service state prevents a dead downloader from causing an infinite wait.
            download_unit = os.environ.get('QA_DOWNLOAD_UNIT')
            if download_unit:
                while subprocess.run(['systemctl', '--user', 'is-active', '--quiet', download_unit]).returncode == 0:
                    status(run, 'job', 'running', active_stage='download', waiting_for=download_unit)
                    time.sleep(15)
            downloaded = run / 'download_status.json'
            if not downloaded.exists() or json.loads(downloaded.read_text())['state'] != 'complete':
                subprocess.run([sys.executable, str(run / 'scripts/download_model.py'),
                                '--run', str(run), '--model', str(model_path)], check=True)
            manifest = json.loads((run / 'model_manifest.json').read_text())
            assert manifest['model'] == config['model'] and manifest['revision'] == config['revision']
            for name, record in manifest['files'].items():
                assert sha(model_path / name) == record['sha256'], f'Model file changed: {name}'
            for stage in ['prepare', 'profile', 'baseline', 'train', 'after', 'compare']:
                completion = {'prepare': 'preparation.json', 'profile': 'profile.json',
                              'compare': 'result.json'}.get(stage, f'{stage}_status.json')
                path = run / completion
                if path.exists():
                    recorded = json.loads(path.read_text())
                    if recorded.get('status') == 'passed' or recorded.get('state') in ['complete', 'awaiting_answer_quality_review']:
                        continue
                status(run, 'job', 'running', active_stage=stage)
                log_path = run / f'{stage}.log'
                with log_path.open('a') as log:
                    subprocess.run([sys.executable, str(Path(__file__).resolve()), stage,
                                    '--run', str(run), '--model', str(model_path)],
                                   stdout=log, stderr=subprocess.STDOUT, check=True)
            status(run, 'job', 'complete', active_stage='awaiting_answer_quality_review')
        except Exception as exc:
            status(run, 'job', 'failed', active_stage=stage, error=str(exc),
                   error_type=type(exc).__name__)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['pipeline', 'prepare', 'profile', 'baseline', 'train', 'after', 'compare', 'verify-loss'])
    parser.add_argument('--run', type=Path)
    parser.add_argument('--model', type=Path)
    args = parser.parse_args()
    os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
    os.environ['HF_HUB_OFFLINE'] = '0' if args.stage == 'pipeline' else '1'
    os.environ['HF_HUB_DISABLE_XET'] = '1'
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    os.environ['PYTORCH_ALLOC_CONF'] = 'expandable_segments:True'
    if args.stage == 'verify-loss':
        print(json.dumps(verify_loss()))
        return
    if args.run is None or args.model is None:
        parser.error('--run and --model are required')
    validate(args.run)
    if args.stage == 'pipeline':
        pipeline(args.run, args.model)
    elif args.stage == 'prepare':
        prepare(args.run, args.model)
    elif args.stage == 'profile':
        profile(args.run, args.model)
    elif args.stage in ['baseline', 'after']:
        evaluate(args.run, args.model, args.stage)
    elif args.stage == 'train':
        train(args.run, args.model)
    else:
        compare(args.run)


if __name__ == '__main__':
    main()
