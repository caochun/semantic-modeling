"""Pinned mirror-only download; safe to restart using the Hub download cache."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--model', required=True, type=Path)
    args = parser.parse_args()
    frozen = json.loads((args.run / 'data/frozen.json').read_text())
    endpoint = 'https://hf-mirror.com'
    os.environ['HF_ENDPOINT'] = endpoint
    os.environ['HF_HUB_DISABLE_XET'] = '1'
    os.environ['HF_HUB_DOWNLOAD_TIMEOUT'] = '120'
    started = time.time()
    status = args.run / 'download_status.json'
    save(status, {'state': 'downloading', 'model': frozen['model'],
                  'revision': frozen['revision'], 'endpoint': endpoint,
                  'pid': os.getpid(), 'started_at': started})
    try:
        from huggingface_hub import snapshot_download
        snapshot_download(frozen['model'], revision=frozen['revision'],
                          endpoint=endpoint, local_dir=args.model, max_workers=2,
                          allow_patterns=['*.json', '*.safetensors', '*.jinja',
                                          'README.md', 'LICENSE'])
        index = args.model / 'model.safetensors.index.json'
        weights = sorted(set(json.loads(index.read_text())['weight_map'].values())) \
            if index.exists() else ['model.safetensors']
        files = weights + ['config.json', 'tokenizer.json', 'tokenizer_config.json',
                           'generation_config.json']
        manifest = {'model': frozen['model'], 'revision': frozen['revision'],
                    'endpoint': endpoint, 'files': {}}
        for name in files:
            path = args.model / name
            manifest['files'][name] = {'bytes': path.stat().st_size, 'sha256': sha(path)}
        save(args.run / 'model_manifest.json', manifest)
        save(status, {'state': 'complete', 'model': frozen['model'],
                      'revision': frozen['revision'], 'endpoint': endpoint,
                      'seconds': time.time() - started, 'files': len(files)})
    except Exception as exc:
        save(status, {'state': 'failed', 'error': str(exc),
                      'error_type': type(exc).__name__, 'seconds': time.time() - started})
        raise


if __name__ == '__main__':
    main()
