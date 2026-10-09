"""Detached GPU-server pipeline: mirror download, baseline, disposable QLoRA step."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3] if Path(__file__).resolve().parent.name == 'qwen' else Path(__file__).resolve().parents[1]
RUN = ROOT / 'experiments/qwen3_8b_20261007'
MODEL = ROOT / 'models/Qwen3-8B'
REVISION = 'b968826d9c46dd6066d109eabc6255188de91218'


def status(stage, state, **extra):
    value = {'stage': stage, 'state': state, 'pid': os.getpid(),
             'updated_at_unix': time.time(), 'revision': REVISION, **extra}
    temporary = RUN / 'job.tmp'
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(RUN / 'job.json')
    print(json.dumps(value), flush=True)


def main():
    RUN.mkdir(parents=True, exist_ok=True)
    with (RUN / 'pipeline.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stage = 'download'
        try:
            os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
            os.environ['HF_HUB_DISABLE_XET'] = '1'
            os.environ['TOKENIZERS_PARALLELISM'] = 'false'
            os.environ['PYTORCH_ALLOC_CONF'] = 'expandable_segments:True'
            os.environ['PYTHONUNBUFFERED'] = '1'
            status(stage, 'running', endpoint=os.environ['HF_ENDPOINT'])
            from huggingface_hub import snapshot_download
            snapshot_download('Qwen/Qwen3-8B', revision=REVISION, local_dir=MODEL,
                allow_patterns=['*.json', '*.safetensors', '*.jinja', 'README.md', 'LICENSE'],
                max_workers=2)
            index = json.loads((MODEL / 'model.safetensors.index.json').read_text())
            weights = sorted(set(index['weight_map'].values()))
            manifest = {'revision': REVISION, 'endpoint': os.environ['HF_ENDPOINT'], 'files': {}}
            for name in weights + ['config.json', 'tokenizer.json', 'tokenizer_config.json']:
                path = MODEL / name
                digest = hashlib.sha256()
                with path.open('rb') as source:
                    while chunk := source.read(8 * 1024 * 1024):
                        digest.update(chunk)
                manifest['files'][name] = {'bytes': path.stat().st_size, 'sha256': digest.hexdigest()}
            (RUN / 'model_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
            frozen = subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True)
            (RUN / 'environment.txt').write_text(frozen)
            for stage, input_name in [('baseline', 'eval_prompts.jsonl'), ('profile', 'sft_context.jsonl')]:
                output = RUN / stage
                if stage == 'profile' and (output / 'profile.json').exists():
                    assert json.loads((output / 'profile.json').read_text())['status'] == 'passed'
                    continue
                status(stage, 'running')
                subprocess.run([sys.executable, str(Path(__file__).with_name('qa_local_qwen.py')), stage,
                    '--model', str(MODEL), '--revision', REVISION,
                    '--input', str(RUN / 'data' / input_name), '--output', str(output)], check=True)
                if stage == 'baseline':
                    assert json.loads((output / 'status.json').read_text())['all_complete'], 'Incomplete baseline'
            status('complete', 'succeeded', full_finetuning_started=False,
                   note='Only one disposable optimizer step; no adapter saved.')
        except Exception as exc:
            status(stage, 'failed', error_type=type(exc).__name__, error=str(exc))
            raise


if __name__ == '__main__':
    main()
