"""Detached Qwen3-4B download and full-context QLoRA feasibility profile."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path('/home/chun/semantic-qa')
RUN = ROOT / 'experiments/qwen3_4b_20261007'
MODEL = ROOT / 'models/Qwen3-4B'
REVISION = '1cfa9a7208912126459214e8b04321603b3df60c'


def write_status(stage, state, **extra):
    value = {'stage': stage, 'state': state, 'pid': os.getpid(),
             'updated_at_unix': time.time(), 'model': 'Qwen/Qwen3-4B',
             'revision': REVISION, **extra}
    temp = RUN / 'job.tmp'
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(RUN / 'job.json')
    print(json.dumps(value, ensure_ascii=False), flush=True)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(8 * 1024 * 1024):
            h.update(chunk)
    return {'bytes': path.stat().st_size, 'sha256': h.hexdigest()}


def main():
    RUN.mkdir(parents=True, exist_ok=True)
    with (RUN / 'pipeline.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            os.environ.update(HF_ENDPOINT='https://hf-mirror.com', HF_HUB_DISABLE_XET='1',
                              TOKENIZERS_PARALLELISM='false', PYTORCH_ALLOC_CONF='expandable_segments:True',
                              PYTHONUNBUFFERED='1')
            write_status('download', 'running', endpoint=os.environ['HF_ENDPOINT'])
            from huggingface_hub import snapshot_download
            snapshot_download('Qwen/Qwen3-4B', revision=REVISION, local_dir=MODEL,
                allow_patterns=['*.json', '*.safetensors', '*.jinja', 'README.md', 'LICENSE'], max_workers=2)
            index = json.loads((MODEL / 'model.safetensors.index.json').read_text())
            files = sorted(set(index['weight_map'].values())) + ['config.json', 'tokenizer.json', 'tokenizer_config.json']
            (RUN / 'model_manifest.json').write_text(json.dumps({
                'model': 'Qwen/Qwen3-4B', 'revision': REVISION,
                'endpoint': os.environ['HF_ENDPOINT'], 'files': {name: digest(MODEL / name) for name in files}
            }, ensure_ascii=False, indent=2) + '\n')
            (RUN / 'environment.txt').write_text(subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True))

            write_status('profile', 'running')
            input_path = ROOT / 'experiments/qwen3_8b_20261007/data/sft_context.jsonl'
            output_path = RUN / 'profile_full'
            subprocess.run([sys.executable, str(Path(__file__).with_name('qa_local_qwen.py')), 'profile',
                '--model', str(MODEL), '--revision', REVISION, '--input', str(input_path),
                '--output', str(output_path), '--lora-scope', 'all'], check=True)
            profile = json.loads((output_path / 'profile.json').read_text())
            assert profile['status'] == 'passed'
            write_status('complete', 'succeeded', profile=profile, formal_finetuning_started=False,
                         note='Qwen3-4B full-context disposable optimizer step only; no adapter saved.')
        except Exception as exc:
            write_status('failed', 'failed', error_type=type(exc).__name__, error=str(exc), formal_finetuning_started=False)
            raise


if __name__ == '__main__':
    main()
