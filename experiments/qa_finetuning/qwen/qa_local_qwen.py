"""Local Qwen baseline and one disposable QLoRA feasibility step; never truncate."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import time


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, obj):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def encode(tokenizer, messages, generation=False):
    return tokenizer.apply_chat_template(messages, tokenize=True,
                                         add_generation_prompt=generation,
                                         enable_thinking=False)


def completion_loss(model, ids, prompt_length):
    """Predict every answer token and terminator without projecting prompt logits."""
    import torch
    positions = torch.arange(prompt_length - 1, ids.shape[1] - 1, device=ids.device)
    logits = model(input_ids=ids, use_cache=False, logits_to_keep=positions).logits
    targets = ids[:, prompt_length:]
    assert logits.shape[:2] == targets.shape
    return torch.nn.functional.cross_entropy(logits.float().reshape(-1, logits.shape[-1]),
                                             targets.reshape(-1))


def verify_loss():
    """Compare selected-token loss and gradients to the ordinary masked full loss."""
    import torch
    from transformers import Qwen3Config, Qwen3ForCausalLM
    torch.manual_seed(42)
    model = Qwen3ForCausalLM(Qwen3Config(vocab_size=97, hidden_size=32,
        intermediate_size=64, num_hidden_layers=2, num_attention_heads=4,
        num_key_value_heads=2, head_dim=8, attention_dropout=0.0))
    ids = torch.randint(0, 97, (1, 15))
    labels = ids.clone()
    labels[:, :10] = -100
    expected = model(input_ids=ids, labels=labels, use_cache=False).loss
    expected.backward()
    grads = {n: p.grad.clone() for n, p in model.named_parameters() if p.grad is not None}
    model.zero_grad(set_to_none=True)
    actual = completion_loss(model, ids, 10)
    actual.backward()
    torch.testing.assert_close(actual, expected)
    for name, param in model.named_parameters():
        if name in grads:
            torch.testing.assert_close(param.grad, grads[name], atol=1e-6, rtol=1e-4)
    return {'masked_loss_and_gradient_parity': 'passed', 'loss': actual.item()}


def load(args):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(args.model, local_files_only=True,
        torch_dtype=torch.bfloat16, device_map={'': 0}, attn_implementation='sdpa',
        quantization_config=BitsAndBytesConfig(load_in_4bit=True,
            bnb_4bit_quant_type='nf4', bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16))
    return tokenizer, model


def baseline(args, config):
    import torch
    from transformers import set_seed
    data = rows(args.input)
    assert len({r['id'] for r in data}) == len(data)
    for row in data:
        assert set(row) == {'id', 'context_id', 'messages'}, 'Use answer-free eval_prompts.jsonl'
        assert [m['role'] for m in row['messages']] == ['system', 'user']
    journal = args.output / 'attempts.jsonl'
    previous = rows(journal) if journal.exists() else []
    assert len({r['id'] for r in previous}) == len(previous), 'Duplicate response IDs'
    done = {r['id']: r for r in previous}
    assert set(done) <= {r['id'] for r in data}
    tokenizer, model = load(args)
    model.eval()
    for index, row in enumerate(data):
        if row['id'] in done:
            continue
        tokens = encode(tokenizer, row['messages'], True)
        assert len(tokens) + config['max_new_tokens'] <= 32768
        ids = torch.tensor([tokens], device='cuda')
        set_seed(config['seed'] + index)
        start = time.monotonic()
        with torch.inference_mode():
            output = model.generate(input_ids=ids, attention_mask=torch.ones_like(ids),
                do_sample=True, temperature=0.7, top_p=0.8, top_k=20,
                max_new_tokens=config['max_new_tokens'], repetition_penalty=1.0,
                pad_token_id=tokenizer.eos_token_id, use_cache=True)
        result = output[0, len(tokens):].tolist()
        eos = model.generation_config.eos_token_id
        stop_ids = [eos] if isinstance(eos, int) else eos
        stopped = bool(result and result[-1] in stop_ids)
        answer = tokenizer.decode(result, skip_special_tokens=True)
        event = {'id': row['id'], 'answer': answer, 'input_tokens': len(tokens),
            'output_tokens': len(result), 'finish_reason': 'stop' if stopped else 'length',
            'status': ('ok' if answer.strip() else 'empty') if stopped else 'incomplete',
            'seconds': round(time.monotonic() - start, 3), 'seed': config['seed'] + index}
        with journal.open('a') as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        done[row['id']] = event
        print(json.dumps({k: event[k] for k in ['id', 'status', 'input_tokens', 'output_tokens', 'seconds']}), flush=True)
    answers = [{'id': row['id'], 'answer': done[row['id']]['answer']} for row in data if done[row['id']]['status'] == 'ok']
    (args.output / 'answers.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in answers))
    save(args.output / 'status.json', {'expected': len(data), 'complete': len(answers),
         'all_complete': len(answers) == len(data), 'expert_scored': False,
         'input_tokens': sum(r['input_tokens'] for r in done.values()),
         'output_tokens': sum(r['output_tokens'] for r in done.values())})


def profile(args):
    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    parity = verify_loss()
    tokenizer, model = load(args)
    data = rows(args.input)
    lengths = []
    for index, row in enumerate(data):
        messages = row['messages']
        assert [m['role'] for m in messages] == ['system', 'user', 'assistant']
        full = encode(tokenizer, messages)
        prefix = encode(tokenizer, messages[:-1], True)
        assert full[:len(prefix)] == prefix and len(full) > len(prefix)
        assert len(full) <= 20480, 'Do not truncate complete samples'
        lengths.append((len(full), index, full, len(prefix)))
    eligible = [item for item in lengths if args.max_profile_tokens is None or item[0] <= args.max_profile_tokens]
    if not eligible:
        raise ValueError('No complete sample satisfies --max-profile-tokens')
    length, index, tokens, prompt_length = max(eligible, key=lambda r: r[0])
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True,
        gradient_checkpointing_kwargs={'use_reentrant': False})
    # Frozen large embeddings/head remain BF16; norms stay FP32.
    model.get_input_embeddings().to(dtype=torch.bfloat16)
    model.get_output_embeddings().to(dtype=torch.bfloat16)
    targets = ['q_proj', 'k_proj', 'v_proj', 'o_proj'] if args.lora_scope == 'attention' else \
        ['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj']
    model = get_peft_model(model, LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05,
        target_modules=targets,
        bias='none', task_type='CAUSAL_LM'))
    model.config.use_cache = False
    model.train()
    parameters = [p for p in model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(parameters, lr=5e-5)
    ids = torch.tensor([tokens], device='cuda')
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    start = time.monotonic()
    with torch.autocast('cuda', dtype=torch.bfloat16):
        loss = completion_loss(model, ids, prompt_length)
    assert torch.isfinite(loss), 'Nonfinite loss'
    loss.backward()
    assert all(p.grad is not None and torch.isfinite(p.grad).all() for p in parameters)
    grad_norm = torch.nn.utils.clip_grad_norm_(parameters, 1.0)
    assert torch.isfinite(grad_norm) and grad_norm > 0
    optimizer.step()
    torch.cuda.synchronize()
    save(args.output / 'profile.json', {**parity, 'status': 'passed', 'sample_index': index,
        'samples_validated': len(data), 'full_tokens': length, 'prompt_tokens': prompt_length,
        'supervised_tokens': length - prompt_length, 'loss': loss.item(),
        'gradient_norm_before_clip': grad_norm.item(),
        'trainable_parameters': sum(p.numel() for p in parameters),
        'peak_allocated_mib': torch.cuda.max_memory_allocated() / 1024**2,
        'peak_reserved_mib': torch.cuda.max_memory_reserved() / 1024**2,
        'seconds': time.monotonic() - start, 'optimizer_steps': 1,
        'adapter_saved': False, 'disposable_feasibility_step': True,
        'input_truncation': False, 'batch_size': 1, 'lora_rank': 16,
        'lora_alpha': 32, 'lora_dropout': 0.05, 'learning_rate': 5e-5,
        'optimizer': 'torch.optim.AdamW', 'gradient_checkpointing': 'non_reentrant',
        'frozen_embeddings_and_head_dtype': 'bfloat16', 'lora_scope': args.lora_scope,
        'lora_target_modules': targets, 'max_profile_tokens': args.max_profile_tokens,
        'loss_scope': 'assistant_and_terminator_only'})
    print((args.output / 'profile.json').read_text(), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['baseline', 'profile', 'verify-loss'])
    parser.add_argument('--model', type=Path)
    parser.add_argument('--revision')
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--max-profile-tokens', type=int)
    parser.add_argument('--lora-scope', choices=['all', 'attention'], default='all')
    args = parser.parse_args()
    if args.mode == 'verify-loss':
        print(json.dumps(verify_loss()))
        return
    if any(v is None for v in [args.model, args.revision, args.input, args.output]):
        parser.error('--model, --revision, --input and --output required')
    config = {'mode': args.mode, 'model': str(args.model), 'revision': args.revision,
        'input_sha256': sha(args.input), 'runner_sha256': sha(Path(__file__)),
        'enable_thinking': False, 'quantization': 'nf4_double_quant_bf16',
        'attention': 'sdpa', 'input_truncation': False, 'seed': 42,
        'max_profile_tokens': args.max_profile_tokens, 'lora_scope': args.lora_scope,
        'max_new_tokens': 4096, 'sampling': {'temperature': 0.7, 'top_p': 0.8, 'top_k': 20},
        'versions': {p: importlib.metadata.version(p) for p in
                     ['torch', 'transformers', 'peft', 'bitsandbytes', 'accelerate', 'tokenizers']}}
    args.output.mkdir(parents=True, exist_ok=True)
    lock = args.output / '.running'
    with lock.open('x') as handle:
        handle.write(str(os.getpid()))
    try:
        manifest = args.output / 'run.json'
        if manifest.exists():
            assert json.loads(manifest.read_text()) == config, 'Run configuration changed'
            if args.mode == 'profile' and (args.output / 'profile.json').exists():
                raise ValueError('Profile already completed')
        else:
            save(manifest, config)
            (args.output / 'input.jsonl').write_bytes(args.input.read_bytes())
        from transformers import set_seed
        set_seed(config['seed'])
        if args.mode == 'baseline':
            baseline(args, config)
        else:
            profile(args)
    except Exception as exc:
        save(args.output / 'error.json', {'type': type(exc).__name__, 'message': str(exc)})
        raise
    finally:
        lock.unlink()


if __name__ == '__main__':
    main()
