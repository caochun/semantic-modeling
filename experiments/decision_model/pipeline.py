"""Queue smoke, fresh-process reload and pilot behind existing GPU work."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .data import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--wait-service', action='append', default=[])
    args = parser.parse_args()
    args.run.mkdir(parents=True, exist_ok=True)
    status_path = args.run / 'pipeline_status.json'
    if status_path.exists():
        parser.error('Use a new run directory; a pipeline already has status here')

    def status(stage, **kwargs):
        value = {'stage': stage, 'updated_at': time.time(), 'pid': os.getpid(), **kwargs}
        tmp = status_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        tmp.replace(status_path)
        print(json.dumps(value, ensure_ascii=False), flush=True)

    def wait_gpu():
        idle_checks = 0
        while idle_checks < 2:
            services = {}
            for service in args.wait_service:
                # RemainAfterExit services stay "active" after the work ends. MainPID
                # is zero only after the supervisor finishes all train/eval stages.
                result = subprocess.run(['systemctl', '--user', 'show', service,
                                         '--property=MainPID', '--value'],
                                        capture_output=True, text=True, check=True)
                services[service] = int(result.stdout.strip())
            result = subprocess.run(['nvidia-smi', '--query-compute-apps=pid,used_memory',
                                     '--format=csv,noheader,nounits'],
                                    capture_output=True, text=True, check=True)
            processes = result.stdout.strip().splitlines()
            idle_checks = idle_checks + 1 if not any(services.values()) and not processes else 0
            if idle_checks < 2:
                status('waiting_for_gpu', existing_services=services, gpu_processes=processes)
                time.sleep(30)

    try:
        status('waiting_for_model')
        while True:
            path = args.model_dir / 'download_status.json'
            download = json.loads(path.read_text()) if path.exists() else {}
            if download.get('stage') == 'complete':
                break
            if download.get('stage') == 'failed':
                raise RuntimeError(f'Model download failed: {download}')
            time.sleep(30)
        env = os.environ.copy()
        env.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                    'HF_HUB_DISABLE_TELEMETRY': '1', 'PYTHONUNBUFFERED': '1',
                    'OMP_NUM_THREADS': '4', 'MKL_NUM_THREADS': '4',
                    'TOKENIZERS_PARALLELISM': 'false'})
        for name, module, flags in [
            ('smoke', 'train', ['--output', str(args.run / 'smoke'), '--model-dir', str(args.model_dir), '--smoke-test']),
            ('smoke_reload', 'verify_reload', ['--run', str(args.run / 'smoke')]),
            ('pilot', 'train', ['--output', str(args.run / 'pilot'), '--model-dir', str(args.model_dir)]),
            ('pilot_reload', 'verify_reload', ['--run', str(args.run / 'pilot')]),
        ]:
            wait_gpu()
            command = [sys.executable, '-u', '-m', f'experiments.decision_model.{module}', *flags]
            status('running', active_stage=name, command=command)
            with (args.run / f'{name}.log').open('w') as log:
                subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                               stderr=subprocess.STDOUT, check=True)
        status('complete', smoke_passed=True, pilot_completed=True, reload_verified=True)
    except Exception as exc:
        status('failed', error_type=type(exc).__name__, error=str(exc))
        raise


if __name__ == '__main__':
    main()
