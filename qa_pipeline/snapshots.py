"""Verify copied QA records without changing the historical review verdicts.

Versioned snapshots omit raw API prompts/responses. Their original hashes are
retained as external bindings; verification of those bytes requires the local
archive. This command checks identity and consistency, not semantic accuracy.
"""
import argparse
import json
from pathlib import Path

from .paths import ROOT
from .quality_pilot import digest, read_rows, sha, validate_record
from .verify_quality import verify_source_reviews


def contained(directory, name):
    path = (directory / name).resolve()
    if not path.is_relative_to(directory.resolve()) or path == directory.resolve():
        raise ValueError('Snapshot path escapes its directory: ' + name)
    return path


def namespace(directory):
    receipt = directory / 'snapshot.json'
    if receipt.is_file():
        return json.loads(receipt.read_text()).get('namespace', directory.name)
    return directory.name


def verify(directory, with_archive=False, project_root=ROOT):
    directory = directory.resolve()
    receipt = json.loads((directory / 'snapshot.json').read_text())
    if receipt['kind'] not in {'qa_batch_snapshot', 'qa_release_snapshot'}:
        raise ValueError('Unknown snapshot kind')
    for name, expected in receipt['files'].items():
        if sha(contained(directory, name)) != expected:
            raise ValueError('Snapshot file changed: ' + name)
    required = {'manifest.json', 'chains.json', 'source_reviews.json'}
    required.add('candidates.jsonl' if receipt['kind'] == 'qa_batch_snapshot' else 'qa.jsonl')
    if not required <= set(receipt['files']):
        raise ValueError('Snapshot lacks required QA/review files')
    manifest = json.loads((directory / 'manifest.json').read_text())
    reviews = json.loads((directory / 'source_reviews.json').read_text())
    omitted = {}
    for bindings in [manifest['files'], reviews['bound_files']]:
        for name, expected in bindings.items():
            if name in receipt['files']:
                if receipt['files'][name] != expected:
                    raise ValueError('Historical binding differs from snapshot: ' + name)
            elif receipt.get('external_review_bindings', {}).get(name) == expected:
                omitted[name] = expected
            else:
                raise ValueError('Unaccounted historical review binding: ' + name)
    if with_archive:
        origin = contained(project_root, receipt['origin_directory'])
        for name, expected in omitted.items():
            if sha(contained(origin, name)) != expected:
                raise ValueError('Archived review evidence changed: ' + name)
    rows = read_rows(directory / ('candidates.jsonl' if receipt['kind'] == 'qa_batch_snapshot' else 'qa.jsonl'))
    if len({r['id'] for r in rows}) != len(rows) or len(rows) != manifest['qa']:
        raise ValueError('Duplicate questions or incorrect QA count')
    verify_source_reviews(rows, reviews)
    chains = {c['id']: c for c in json.loads((directory / 'chains.json').read_text())}
    for row in rows:
        validate_record(row, chains[row['business_unit_id']])
        if row.get('training_eligible') is not False:
            raise ValueError('Snapshot is not a training release')
    batch_results = []
    if receipt['kind'] == 'qa_release_snapshot':
        by_batch = {}
        for batch in manifest['batches']:
            original = Path(batch['directory']).name
            mapped = receipt['batch_snapshots'].get(original)
            if not mapped:
                raise ValueError('Missing batch snapshot mapping: ' + original)
            path = contained(project_root, mapped)
            result = verify(path, with_archive=with_archive, project_root=project_root)
            for name, key in [('manifest.json', 'manifest_sha256'), ('source_reviews.json', 'source_reviews_sha256')]:
                if sha(path / name) != batch[key]:
                    raise ValueError('Release points to a changed batch: ' + original)
            by_batch[batch['directory']] = (
                {r['id']: r for r in read_rows(path / 'candidates.jsonl')},
                {r['id']: r for r in json.loads((path / 'source_reviews.json').read_text())['rows']})
            batch_results.append(result)
        source_reviews = {r['id']: r for r in reviews['rows']}
        for row in rows:
            original_rows, original_reviews = by_batch[row['origin']['dataset']]
            original_id = row['origin']['id']
            original = original_rows[original_id]
            if digest(original) != row['origin']['candidate_sha256']:
                raise ValueError('Release origin candidate changed: ' + row['id'])
            for field in ['question', 'answer', 'history', 'required_fact_ids']:
                if row.get(field) != original.get(field):
                    raise ValueError('Release question/answer/context changed: ' + row['id'])
            if source_reviews[row['id']]['origin_review_sha256'] != digest(original_reviews[original_id]):
                raise ValueError('Release origin review changed: ' + row['id'])
        if sum(b['qa'] for b in batch_results) != len(rows):
            raise ValueError('Release omits a batch question')
    return {'kind': receipt['kind'], 'qa': len(rows), 'business_units': len(chains),
            'bound_source_reviews_consistent': True,
            'external_binding_files': len(omitted) + sum(b['external_binding_files'] for b in batch_results),
            'external_bindings_checked_against_archive': with_archive,
            'batch_snapshots_checked': len(batch_results),
            'expert_review': 'not_performed', 'training_eligible': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--with-archive', action='store_true',
                        help='Also verify omitted review evidence in the original local directories')
    args = parser.parse_args()
    print(json.dumps(verify(args.dataset, args.with_archive), ensure_ascii=False))


if __name__ == '__main__':
    main()
