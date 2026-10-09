"""Detached Qwen3-4B QLoRA SFT with assistant-only loss and resumable checkpoints."""
import hashlib
import json
import os
from pathlib import Path
import random
import time

import torch
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, get_linear_schedule_with_warmup, set_seed

ROOT = Path('/home/chun/semantic-qa')
MODEL = ROOT / 'models/Qwen3-4B'
DATA = Path(os.environ.get('QA_TRAIN_DATA', str(ROOT / 'experiments/qwen3_8b_20261007/data/sft_context.jsonl')))
BASELINE_STATUS = ROOT / 'experiments/qwen3_4b_20261007/baseline/status.json'
OUT = Path(os.environ.get('QA_TRAIN_OUT', str(ROOT / 'experiments/qwen3_4b_20261007/train')))
REVISION = '1cfa9a7208912126459214e8b04321603b3df60c'
SEED = 42
EPOCHS = 2
GROUP_SIZE = 8
SAVE_EVERY_STEPS = 5
LR = 5e-5


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def encode(tokenizer, messages, generation=False):
    return tokenizer.apply_chat_template(messages, tokenize=True,
        add_generation_prompt=generation, enable_thinking=False)


def completion_loss(model, ids, prompt_length):
    positions = torch.arange(prompt_length - 1, ids.shape[1] - 1, device=ids.device)
    logits = model(input_ids=ids, use_cache=False, logits_to_keep=positions).logits
    targets = ids[:, prompt_length:]
    assert logits.shape[:2] == targets.shape
    return torch.nn.functional.cross_entropy(logits.float().reshape(-1, logits.shape[-1]), targets.reshape(-1))


def load_model():
    tokenizer = AutoTokenizer.from_pretrained(MODEL, local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(MODEL, local_files_only=True,
        torch_dtype=torch.bfloat16, device_map={'': 0}, attn_implementation='sdpa',
        quantization_config=BitsAndBytesConfig(load_in_4bit=True,
            bnb_4bit_quant_type='nf4', bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16))
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True,
        gradient_checkpointing_kwargs={'use_reentrant': False})
    model.get_input_embeddings().to(dtype=torch.bfloat16)
    model.get_output_embeddings().to(dtype=torch.bfloat16)
    model = get_peft_model(model, LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05,
        target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj'],
        bias='none', task_type='CAUSAL_LM'))
    model.config.use_cache = False
    return tokenizer, model


def checkpoint(model, optimizer, scheduler, epoch, step, sample_count, losses):
    path = OUT / f'checkpoint-{step:05d}'
    path.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(path)
    torch.save({'optimizer': optimizer.state_dict(), 'scheduler': scheduler.state_dict(),
                'torch_rng': torch.get_rng_state(), 'cuda_rng': torch.cuda.get_rng_state_all(),
                'python_rng': random.getstate()}, path / 'training_state.pt')
    save_json(path / 'state.json', {'epoch': epoch, 'optimizer_step': step,
        'sample_count': sample_count, 'losses': losses, 'revision': REVISION})
    save_json(OUT / 'latest.json', {'checkpoint': str(path), 'epoch': epoch,
        'optimizer_step': step, 'sample_count': sample_count})


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    save_json(OUT / 'config.json', {'model': 'Qwen/Qwen3-4B', 'revision': REVISION,
        'data_sha256': sha(DATA), 'epochs': EPOCHS, 'group_size': GROUP_SIZE,
        'lora_rank': 16, 'lora_alpha': 32, 'lora_dropout': 0.05, 'learning_rate': LR,
        'gradient_checkpointing': 'non_reentrant', 'quantization': 'nf4_double_quant_bf16',
        'input_truncation': False, 'enable_thinking': False, 'loss_scope': 'assistant_and_terminator_only',
        'formal_training': True, 'checkpoint_every_optimizer_steps': SAVE_EVERY_STEPS})
    while True:
        if BASELINE_STATUS.exists():
            status = json.loads(BASELINE_STATUS.read_text())
            if status.get('all_complete') is True:
                break
            if status.get('complete', 0) and status.get('complete') != status.get('expected'):
                pass
        save_json(OUT / 'status.json', {'state': 'waiting_for_baseline', 'checked_at': time.time()})
        time.sleep(30)
    set_seed(SEED)
    data = rows(DATA)
    tokenizer, model = load_model()
    prepared = []
    for row_index, row in enumerate(data):
        messages = row['messages']
        full = encode(tokenizer, messages)
        prefix = encode(tokenizer, messages[:-1], True)
        assert full[:len(prefix)] == prefix
        assert len(full) <= 32768
        prepared.append((row.get('id', str(row_index)), full, len(prefix)))
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=LR)
    total_steps = EPOCHS * ((len(data) + GROUP_SIZE - 1) // GROUP_SIZE)
    scheduler = get_linear_schedule_with_warmup(optimizer, num_warmup_steps=2, num_training_steps=total_steps)
    model.train()
    step = 0
    sample_count = 0
    losses = []
    started = time.monotonic()
    for epoch in range(EPOCHS):
        order = list(range(len(prepared)))
        random.Random(SEED + epoch).shuffle(order)
        for start in range(0, len(order), GROUP_SIZE):
            group = order[start:start + GROUP_SIZE]
            optimizer.zero_grad(set_to_none=True)
            group_losses = []
            for index in group:
                _, tokens, prompt_length = prepared[index]
                ids = torch.tensor([tokens], device='cuda')
                with torch.autocast('cuda', dtype=torch.bfloat16):
                    loss = completion_loss(model, ids, prompt_length)
                assert torch.isfinite(loss)
                (loss / len(group)).backward()
                group_losses.append(loss.item())
                sample_count += 1
            grad_norm = torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0)
            assert torch.isfinite(grad_norm)
            optimizer.step()
            scheduler.step()
            step += 1
            losses.append({'epoch': epoch, 'step': step, 'mean_loss': sum(group_losses) / len(group_losses),
                           'grad_norm': grad_norm.item(), 'sample_count': sample_count})
            save_json(OUT / 'status.json', {'state': 'training', 'epoch': epoch, 'optimizer_step': step,
                'sample_count': sample_count, 'latest_loss': losses[-1]['mean_loss'],
                'elapsed_seconds': time.monotonic() - started})
            if step % SAVE_EVERY_STEPS == 0 or (epoch == EPOCHS - 1 and start + GROUP_SIZE >= len(order)):
                checkpoint(model, optimizer, scheduler, epoch, step, sample_count, losses)
            del ids
            torch.cuda.empty_cache()
    checkpoint(model, optimizer, scheduler, EPOCHS - 1, step, sample_count, losses)
    save_json(OUT / 'status.json', {'state': 'complete', 'epoch': EPOCHS, 'optimizer_step': step,
        'sample_count': sample_count, 'elapsed_seconds': time.monotonic() - started,
        'latest_checkpoint': str(OUT / f'checkpoint-{step:05d}'), 'formal_training': True})


if __name__ == '__main__':
    main()
