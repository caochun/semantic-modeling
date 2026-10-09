"""Migrated snapshots must retain bindings and reject missing/changed evidence."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from qa_pipeline.paths import ROOT
from qa_pipeline.__main__ import COMMANDS
from qa_pipeline.quality_pilot import sha
from qa_pipeline.snapshots import namespace, verify


class SnapshotIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / 'renamed-batch'
        shutil.copytree(ROOT / 'data/qa/batches/20261008/batch01', self.directory)

    def receipt(self):
        return json.loads((self.directory / 'snapshot.json').read_text())

    def write_receipt(self, value):
        (self.directory / 'snapshot.json').write_text(json.dumps(value))

    def test_offline_snapshot_keeps_namespace_and_declares_external_evidence(self):
        result = verify(self.directory)
        self.assertEqual(result['qa'], 18)
        self.assertGreater(result['external_binding_files'], 0)
        self.assertFalse(result['external_bindings_checked_against_archive'])
        self.assertEqual(namespace(self.directory), 'qa_quality_20261008_v3')
        self.assertFalse(result['training_eligible'])

    def test_changed_answer_rejected_before_any_review_status_is_reused(self):
        path = self.directory / 'candidates.jsonl'
        text = path.read_text()
        row = json.loads(text.splitlines()[0])
        row['answer'] = '更改后的无来源答案'
        path.write_text(json.dumps(row) + '\n' + '\n'.join(text.splitlines()[1:]))
        with self.assertRaisesRegex(ValueError, 'Snapshot file changed'):
            verify(self.directory)

    def test_missing_external_binding_cannot_be_silently_skipped(self):
        receipt = self.receipt()
        receipt['external_review_bindings'].pop(next(iter(receipt['external_review_bindings'])))
        self.write_receipt(receipt)
        with self.assertRaisesRegex(ValueError, 'Unaccounted historical'):
            verify(self.directory)

    def test_manifest_cannot_rebind_review_inputs_using_only_a_new_snapshot_hash(self):
        manifest = self.directory / 'manifest.json'
        value = json.loads(manifest.read_text())
        value['files']['candidates.jsonl'] = '0' * 64
        manifest.write_text(json.dumps(value))
        receipt = self.receipt()
        receipt['files']['manifest.json'] = sha(manifest)
        self.write_receipt(receipt)
        with self.assertRaisesRegex(ValueError, 'Historical binding differs'):
            verify(self.directory)

    def test_path_escape_rejected_even_when_file_checksum_is_valid(self):
        outside = self.directory.parent / 'outside.txt'
        outside.write_text('outside')
        receipt = self.receipt()
        receipt['files']['../outside.txt'] = sha(outside)
        self.write_receipt(receipt)
        with self.assertRaisesRegex(ValueError, 'escapes'):
            verify(self.directory)

    def test_every_command_has_an_offline_help_entry_point(self):
        for command in COMMANDS:
            with self.subTest(command=command):
                result = subprocess.run([sys.executable, '-m', 'qa_pipeline', command, '--help'],
                                        cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout)


if __name__ == '__main__':
    unittest.main()
