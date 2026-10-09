"""Evaluate a saved Qwen3 LoRA adapter with the exact local baseline protocol."""
import hashlib
import json
import os
from pathlib import Path
import time

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, set_seed

ROOT = Path('/home/chun/semantic-qa')
MODEL = ROOT / 'models/Qwen3-4B'
ADAPTER = ROOT / 'experiments/qwen3_4b_20261007/train/checkpoint-00038'
INPUT = ROOT / 'experiments/qwen3_8b_20261007/data/eval_prompts.jsonl'
OUT = ROOT / 'experiments/qwen3_4b_20261007/adapter_eval'
REVISION = '1cfa9a7208912126459214e8b04321603b3df60c'


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    data = rows(INPUT)
    previous = rows(OUT / 'attempts.jsonl') if (OUT / 'attempts.jsonl').exists() else []
    done = {r['id']: r for r in previous}
    assert len(done) == len(previous) and set(done) <= {r['id'] for r in data}
    tokenizer = AutoTokenizer.from_pretrained(MODEL, local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(MODEL, local_files_only=True,
        torch_dtype=torch.bfloat16, device_map={'': 0}, attn_implementation='sdpa',
        quantization_config=BitsAndBytesConfig(load_in_4bit=True,
            bnb_4bit_quant_type='nf4', bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16))
    model = PeftModel.from_pretrained(model, ADAPTER, is_trainable=False)
    model.eval()
    for index, row in enumerate(data):
        if row['id'] in done:
            continue
        tokens = tokenizer.apply_chat_template(row['messages'], tokenize=True,
            add_generation_prompt=True, enable_thinking=False)
        assert len(tokens) + 4096 <= 32768
        ids = torch.tensor([tokens], device='cuda')
        set_seed(42 + index)
        start = time.monotonic()
        with torch.inference_mode():
            output = model.generate(input_ids=ids, attention_mask=torch.ones_like(ids),
                do_sample=True, temperature=0.7, top_p=0.8, top_k=20,
                max_new_tokens=4096, pad_token_id=tokenizer.eos_token_id, use_cache=True)
        generated = output[0, len(tokens):].tolist()
        eos = model.generation_config.eos_token_id
        stop_ids = [eos] if isinstance(eos, int) else eos
        stopped = bool(generated and generated[-1] in stop_ids)
        answer = tokenizer.decode(generated, skip_special_tokens=True)
        event = {'id': row['id'], 'answer': answer, 'input_tokens': len(tokens),
            'output_tokens': len(generated), 'finish_reason': 'stop' if stopped else 'length',
            'status': ('ok' if answer.strip() else 'empty') if stopped else 'incomplete',
            'seconds': round(time.monotonic() - start, 3), 'seed': 42 + index}
        with (OUT / 'attempts.jsonl').open('a') as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        done[row['id']] = event
        print(json.dumps({k: event[k] for k in ['id', 'status', 'input_tokens', 'output_tokens', 'seconds']}), flush=True)
    answers = [{'id': row['id'], 'answer': done[row['id']]['answer']} for row in data if done[row['id']]['status'] == 'ok']
    (OUT / 'answers.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in answers))
    save(OUT / 'status.json', {'expected': len(data), 'complete': len(answers),
        'all_complete': len(answers) == len(data), 'adapter': str(ADAPTER),
        'model': 'Qwen/Qwen3-4B', 'revision': REVISION,
        'input_sha256': hashlib.sha256(INPUT.read_bytes()).hexdigest(), 'expert_scored': False})


if __name__ == '__main__':
    main()
