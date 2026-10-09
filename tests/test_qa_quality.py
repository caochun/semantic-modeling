"""Verify that traceability checks reject omissions and changed frozen inputs.

These tests do not establish business correctness; model/source reviews do that.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from qa_pipeline import quality_pilot as quality
from qa_pipeline.verify_quality import CHECKS, verify_source_reviews
from qa_pipeline.review_quality import decode_json


class QualityIntegrityTests(unittest.TestCase):
    def test_complete_json_survives_missing_closing_markdown_fence(self):
        self.assertEqual(decode_json('```json\n{"reviews": []}'), {'reviews': []})

    def test_truncated_json_is_not_repaired_when_fence_is_missing(self):
        with self.assertRaises(json.JSONDecodeError):
            decode_json('```json\n{"reviews": [')

    def setUp(self):
        self.chain = {'facts': [{'id': name, 'text': name} for name in ['stop', 'permit', 'restore']],
                      'order_edges': [['stop', 'permit'], ['permit', 'restore']]}
        self.row = {'id': 'fixture', 'type': 'full_chain',
                    'required_fact_ids': ['stop', 'permit', 'restore'],
                    'required_order_edges': self.chain['order_edges'],
                    'answer_segments': [{'text': name, 'fact_ids': [name]} for name in ['stop', 'permit', 'restore']],
                    'answer': 'stop\npermit\nrestore'}

    def test_omitted_restoration_rejected_even_if_remaining_text_has_source(self):
        row = copy.deepcopy(self.row)
        row['answer_segments'].pop()
        with self.assertRaisesRegex(ValueError, 'omit'):
            quality.validate_record(row, self.chain)

    def test_permission_after_restoration_rejected(self):
        row = copy.deepcopy(self.row)
        row['answer_segments'][1:] = reversed(row['answer_segments'][1:])
        with self.assertRaisesRegex(ValueError, 'order'):
            quality.validate_record(row, self.chain)

    def test_incomplete_whole_chain_plan_rejected(self):
        row = copy.deepcopy(self.row)
        row['required_fact_ids'].pop()
        row['answer_segments'].pop()
        row['required_order_edges'] = [['stop', 'permit']]
        with self.assertRaisesRegex(ValueError, 'Full-chain'):
            quality.validate_record(row, self.chain)

    def test_unknown_fact_and_untraceable_segment_rejected(self):
        row = copy.deepcopy(self.row)
        row['required_fact_ids'].append('invented')
        with self.assertRaises(ValueError):
            quality.validate_record(row, self.chain)

    def test_repeated_operational_sentence_rejected(self):
        row = copy.deepcopy(self.row)
        row['answer'] = '\n'.join(['这是重复的操作句子且不应出现三次以上'] * 3)
        with self.assertRaisesRegex(ValueError, 'Repeated'):
            quality.validate_record(row, self.chain)

    def test_changed_source_input_has_different_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'source.json'
            path.write_text(json.dumps({'condition': '正常且无下降趋势'}, ensure_ascii=False))
            original = quality.sha(path)
            path.write_text(json.dumps({'condition': '迅速下降'}, ensure_ascii=False))
            self.assertNotEqual(quality.sha(path), original)

    def test_changed_answer_invalidates_previous_review(self):
        row = {'id': 'fixture', 'answer': '先取得调度许可再恢复'}
        review = {'rows': [{'id': 'fixture', 'candidate_sha256': quality.digest(row),
                           'checks': {key: 'pass' for key in CHECKS},
                           'unresolved_issues': [], 'notes': '已按来源核对许可顺序'}]}
        row['answer'] = '先恢复再汇报'
        with self.assertRaisesRegex(ValueError, 'Stale'):
            verify_source_reviews([row], review)

    def test_uncertain_critical_condition_blocks_reviewed_status(self):
        row = {'id': 'fixture', 'answer': '依据待确认的条件切换'}
        checks = {key: 'pass' for key in CHECKS}
        checks['condition_branches'] = 'uncertain'
        review = {'rows': [{'id': 'fixture', 'candidate_sha256': quality.digest(row),
                           'checks': checks, 'unresolved_issues': [], 'notes': '条件尚未确定'}]}
        with self.assertRaisesRegex(ValueError, 'Unresolved'):
            verify_source_reviews([row], review)

    def test_missing_review_cannot_promote_whole_batch(self):
        with self.assertRaisesRegex(ValueError, 'Missing'):
            verify_source_reviews([{'id': 'unreviewed'}], {'rows': []})

    def test_all_pass_checks_do_not_hide_unreviewed_required_fact(self):
        row = {'id': 'fixture', 'answer': '许可后恢复', 'required_fact_ids': ['permit', 'restore']}
        review = {'rows': [{'id': 'fixture', 'candidate_sha256': quality.digest(row),
                           'checks': {key: 'pass' for key in CHECKS}, 'required_facts_reviewed': ['permit'],
                           'unresolved_issues': [], 'notes': '只核对了许可'}]}
        with self.assertRaisesRegex(ValueError, 'individually'):
            verify_source_reviews([row], review)

    def test_direct_source_selector_preserves_exact_offsets(self):
        sources = {'s': {'source_id': 's', 'blocks': [
            {'id': 'b', 'kind': 'paragraph', 'text': '许可后恢复', 'locator': 'test'}]}}
        result = quality.source_span(sources, {'source_id': 's', 'block_id': 'b', 'start': 2, 'end': 5})
        self.assertEqual(result['text'], '后恢复')
        self.assertEqual(result['id'], 's/b:2:5')

    def test_direct_source_selector_rejects_truncated_and_unparsed_input(self):
        sources = {'s': {'source_id': 's', 'blocks': [
            {'id': 'b', 'kind': 'paragraph', 'text': '许可后恢复', 'locator': 'test'}]}}
        selector = {'source_id': 's', 'block_id': 'b', 'start': 0, 'end': 10}
        with self.assertRaisesRegex(ValueError, 'offsets'):
            quality.source_span(sources, selector)
        selector['end'] = 5
        sources['s']['blocks'][0]['kind'] = 'legacy_table_linearized'
        with self.assertRaisesRegex(ValueError, 'Uninterpreted'):
            quality.source_span(sources, selector)


if __name__ == '__main__':
    unittest.main()
