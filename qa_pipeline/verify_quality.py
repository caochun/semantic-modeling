"""Verify a quality pilot and bound per-question source-review records.

This verifies artifact identity and review completeness, not semantic accuracy.
It never exports SFT data or initiates training.
"""
import argparse
import json
from pathlib import Path

from .quality_pilot import digest, read_rows, sha, validate_record

CHECKS = {'question_clear', 'source_support', 'required_coverage', 'condition_branches',
          'step_order', 'prohibitions_and_permissions', 'restoration', 'expanded_context',
          'history_dependency'}


def verify_source_reviews(candidates, source_reviews):
    reviews = source_reviews['rows']
    by_id = {r['id']: r for r in reviews}
    if len(by_id) != len(reviews) or set(by_id) != {r['id'] for r in candidates}:
        raise ValueError('Missing, additional or duplicate source-review records')
    for row in candidates:
        review = by_id[row['id']]
        if review['candidate_sha256'] != digest(row):
            raise ValueError(f"Stale source review: {row['id']}")
        if set(review['checks']) != CHECKS or any(v != 'pass' for v in review['checks'].values()):
            raise ValueError(f"Unresolved semantic review: {row['id']}")
        if review['unresolved_issues'] or not review['notes'].strip():
            raise ValueError(f"Unresolved or undocumented source review: {row['id']}")
        if ('required_fact_ids' in row and
                set(review.get('required_facts_reviewed', [])) != set(row['required_fact_ids'])):
            raise ValueError(f"Required facts not individually reviewed: {row['id']}")
    return len(reviews)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    args = parser.parse_args()
    directory = args.dataset.resolve()
    manifest = json.loads((directory / 'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        assert sha(directory / name) == expected, f'Frozen input changed: {name}'
    candidates = read_rows(directory / 'candidates.jsonl')
    chains = {c['id']: c for c in json.loads((directory / 'chains.json').read_text())}
    for row in candidates:
        validate_record(row, chains[row['business_unit_id']])
        assert row['training_eligible'] is False
    for stage in ['fact_review', 'blind_reanswer', 'semantic_review']:
        folder = directory / stage
        config = json.loads((folder / 'run.json').read_text())
        prompts = read_rows(folder / 'prompts.jsonl')
        responses = read_rows(folder / 'responses.jsonl')
        assert digest(prompts) == config['prompts_sha256']
        assert len({r['id'] for r in responses}) == len(responses)
        by_id = {r['id']: r for r in responses}
        assert set(by_id) == {r['id'] for r in prompts}
        for prompt in prompts:
            response = by_id[prompt['id']]
            assert response['prompt_sha256'] == digest(prompt) and response['status'] == 'ok'
    source_reviews = json.loads((directory / 'source_reviews.json').read_text())
    for name, expected in source_reviews['bound_files'].items():
        assert sha(directory / name) == expected, f'Review evidence changed: {name}'
    count = verify_source_reviews(candidates, source_reviews)
    result = {'state': 'small_pilot_document_fidelity_reviewed', 'qa': count,
              'chains': len(chains), 'source_reviewer': source_reviews['reviewer'],
              'expert_review': 'not_performed', 'semantic_accuracy_rate': 'not_estimated',
              'source_currency_certified': False, 'training_eligible': False,
              'training_export_created': False,
              'note': 'Small purposefully selected pilot; review records bound to exact inputs. Not a whole-corpus quality estimate.'}
    (directory / 'quality_status.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
