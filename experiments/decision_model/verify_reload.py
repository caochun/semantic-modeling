"""Verify a saved adapter and decision head in a fresh GPU process."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contract_check import LOCK
from .data import write_json
from .download import verify_model
from .runtime_check import compare_predictions
from .train import runtime_source_hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads((args.run / 'run_config.json').read_text())
    lock = json.loads(LOCK.read_text())
    if config.get('model_dir'):
        verify_model(Path(config['model_dir']), lock['pilot_model'])
    import unsloth
    from unsloth import FastDecisionModel
    runtime_source_hashes(Path(unsloth.__file__).resolve().parent, lock)
    reference = json.loads((args.run / 'reload_reference.json').read_text())
    try:
        model, tokenizer = FastDecisionModel.from_pretrained(
            model_name=str(args.run / 'adapter'), max_seq_length=config['max_length'],
            load_in_4bit=True, local_files_only=bool(config.get('model_dir')))
        FastDecisionModel.for_inference(model)
        actual = [FastDecisionModel.predict(model, tokenizer, row['state'], row['questions'])
                  for row in reference['probes']]
        write_json(args.run / 'reload_actual.json', actual)
        report = compare_predictions(reference['predictions'], actual, reference['absolute_tolerance'])
        write_json(args.run / 'reload_verification.json', report)
        if not report['passed']:
            raise ValueError(f'Saved model predictions exceed tolerance: {report}')
        write_json(args.run / 'status.json', {'stage': 'complete', 'training_completed': True,
                                             'reload_verified': True, 'smoke_test': config['smoke_test']})
    except Exception as exc:
        write_json(args.run / 'status.json', {'stage': 'reload_failed', 'training_completed': True,
                                             'reload_verified': False, 'error': str(exc)})
        raise


if __name__ == '__main__':
    main()
