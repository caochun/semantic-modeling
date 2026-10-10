"""Download the locked pilot model and record every local payload hash."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from .contract_check import LOCK
from .data import write_json


def payload_digest(path):
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_model(folder, locked):
    manifest = json.loads((folder / 'download_manifest.json').read_text())
    if manifest['model'] != locked:
        raise ValueError('Downloaded model does not match upstream.lock.json')
    for name, expected in manifest['files'].items():
        path = folder / name
        if not path.is_file() or payload_digest(path) != expected['sha256']:
            raise ValueError(f'Model payload missing or changed: {name}')
    index = folder / 'model.safetensors.index.json'
    weights = set(json.loads(index.read_text())['weight_map'].values()) if index.exists() else {'model.safetensors'}
    required = weights | {'config.json', 'tokenizer.json', 'tokenizer_config.json'}
    if not required <= manifest['files'].keys():
        raise ValueError('Incomplete model manifest')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--endpoint', default='https://hf-mirror.com')
    args = parser.parse_args()
    locked = json.loads(LOCK.read_text())['pilot_model']
    os.environ['HF_ENDPOINT'] = args.endpoint
    os.environ['HF_HUB_DISABLE_XET'] = '1'
    os.environ['HF_HUB_DOWNLOAD_TIMEOUT'] = '120'
    started = time.time()
    args.output.mkdir(parents=True, exist_ok=True)
    status = args.output / 'download_status.json'
    write_json(status, {'stage': 'downloading', 'model': locked, 'pid': os.getpid()})
    try:
        from huggingface_hub import snapshot_download
        snapshot_download(locked['name'], revision=locked['revision'],
                          endpoint=args.endpoint, local_dir=args.output, max_workers=2,
                          allow_patterns=['*.json', '*.safetensors', '*.jinja', '*.txt', '*.model',
                                          'README.md', 'LICENSE*'])
        files = {}
        for path in sorted(args.output.iterdir()):
            if path.is_file() and path.name not in {'download_status.json', 'download_manifest.json'}:
                files[path.name] = {'bytes': path.stat().st_size, 'sha256': payload_digest(path)}
        write_json(args.output / 'download_manifest.json',
                   {'model': locked, 'endpoint': args.endpoint, 'files': files})
        verify_model(args.output, locked)
        write_json(status, {'stage': 'complete', 'model': locked, 'seconds': time.time() - started})
    except Exception as exc:
        write_json(status, {'stage': 'failed', 'error_type': type(exc).__name__, 'error': str(exc)})
        raise


if __name__ == '__main__':
    main()
