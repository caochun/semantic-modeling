"""Coverage must not grow through broad context, stale or incomplete reviews."""
import unittest

from qa_pipeline.coverage_ledger import apply_assessment, pdf_catalog, preface_findings, quote_matches, unit, word_catalog
from qa_pipeline.quality_pilot import digest
from qa_pipeline.review_quality import matching_prompts, validate_advisory
from qa_pipeline.verify_quality import CHECKS


class CoverageIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.source = {'source_id': 'test', 'blocks': []}
        self.block = {'id': 'b', 'text': '先取得许可再恢复。另有恢复后检查要求。', 'locator': 'test'}
        self.item = unit(self.source, 'clause', 'numbered_clause',
                         [{'source_id': 'test', 'block_id': 'b', 'start': 0,
                           'end': len(self.block['text']), 'text': self.block['text'], 'locator': 'test'}])
        self.fact = {'source_id': 'test', 'source_quote': '先取得许可再恢复', 'evidence_ids': ['b:0:100']}
        self.evidence = [{'id': 'b:0:100', 'block_id': 'b', 'start': 0, 'end': 100}]
        self.facts = {'fact': self.fact}
        self.item['fact_links'] = [{'key': 'fact'}]
        self.assessment = {'unit_id': self.item['id'],
                           'unit_sha256': digest({k: self.item[k] for k in ['id', 'text', 'spans']}),
                           'fact_keys': ['fact'], 'facts_sha256': digest([self.fact]),
                           'checks': {k: 'pass' for k in ['all_propositions', 'conditions',
                                     'permissions_prohibitions', 'order_outcomes_restoration', 'scope_numbers']},
                           'notes': 'fixture explicit semantic assessment'}

    def test_broad_context_without_precise_quote_does_not_count(self):
        self.item['spans'][0]['text'] = '另有恢复后检查要求。'
        self.assertFalse(quote_matches(self.item, self.fact, self.evidence))

    def test_quote_presence_does_not_promote_whole_clause(self):
        self.assertTrue(quote_matches(self.item, self.fact, self.evidence))
        self.assertEqual(self.item['qa_coverage'], 'missing_reviewed_qa')

    def test_changed_source_or_fact_invalidates_semantic_assessment(self):
        self.item['text'] += '禁止自动恢复。'
        with self.assertRaisesRegex(ValueError, 'Stale source'):
            apply_assessment(self.item, self.assessment, self.facts)
        self.item['text'] = self.block['text']
        self.fact['source_quote'] = '恢复后检查'
        with self.assertRaisesRegex(ValueError, 'Stale fact'):
            apply_assessment(self.item, self.assessment, self.facts)

    def test_uncertain_prohibition_keeps_clause_incomplete(self):
        self.assessment['checks']['permissions_prohibitions'] = 'uncertain'
        with self.assertRaisesRegex(ValueError, 'Partial'):
            apply_assessment(self.item, self.assessment, self.facts)

    def test_unreviewed_fact_cannot_close_clause(self):
        with self.assertRaisesRegex(ValueError, 'Unreviewed'):
            apply_assessment(self.item, self.assessment, {})

    def test_unparsed_table_cannot_be_promoted_by_asserted_checks(self):
        self.item['kind'] = 'uninterpreted_table_cell'
        with self.assertRaisesRegex(ValueError, 'Uninterpreted'):
            apply_assessment(self.item, self.assessment, self.facts)

    def test_pdf_cross_page_continuation_retained(self):
        self.source['blocks'] = [
            {'id': 'page11', 'text': '3.1 标题\n3.1.1.1 如不能恢复，\n', 'locator': 'p11'},
            {'id': 'page12', 'text': '须申请停电。\n3.1.1.2 恢复后检查。\n', 'locator': 'p12'}]
        units, _ = pdf_catalog(self.source)
        clause = next(u for u in units if u.get('number') == '3.1.1.1')
        self.assertIn('须申请停电', clause['text'])
        self.assertEqual(len(clause['spans']), 2)

    def test_missing_toc_and_unparsed_table_remain_visible(self):
        self.source['blocks'] = [
            {'id': 'toc1', 'kind': 'toc', 'text': '1 范围\t1', 'locator': 'toc'},
            {'id': 'toc2', 'kind': 'toc', 'text': '2 无法还原的章节\t2', 'locator': 'toc'},
            {'id': 'range', 'kind': 'paragraph', 'text': '范围', 'locator': 'body'},
            {'id': 'cell', 'kind': 'legacy_table_linearized', 'text': '300', 'locator': 'table'}]
        units, sections = word_catalog(self.source)
        self.assertFalse(sections[1]['body_heading_found'])
        self.assertEqual(units[-1]['kind'], 'uninterpreted_table_cell')
        self.assertEqual(units[-1]['qa_coverage'], 'missing_reviewed_qa')

    def test_preface_business_parameters_not_excluded_as_front_matter(self):
        self.source['blocks'] = [
            {'id': 'preface', 'kind': 'paragraph', 'text': '前言', 'locator': 'front'},
            {'id': 'rated', 'kind': 'paragraph', 'text': '额定电流5000A', 'locator': 'front'},
            {'id': 'toc', 'kind': 'toc', 'text': '1 范围\t1', 'locator': 'toc'},
            {'id': 'range', 'kind': 'paragraph', 'text': '范围', 'locator': 'body'}]
        units, _ = word_catalog(self.source)
        self.assertEqual(units[1]['kind'], 'preface_business_candidate')
        self.assertEqual(units[1]['qa_coverage'], 'missing_reviewed_qa')

    def test_same_preface_error_is_recorded_for_each_distinct_volume(self):
        for scope in ['第三分册运行方式及设备运行规定', '第五分册设备异常及故障应急处置']:
            source = {'scope': scope, 'blocks': [{'id': 'preface', 'text': '本册为设备概况分册'}]}
            findings = preface_findings(source)
            self.assertEqual(len(findings), 1)
            self.assertIn(scope, findings[0]['note'])
            self.assertEqual(source['blocks'][0]['text'], '本册为设备概况分册')

    def test_matching_equipment_overview_preface_is_not_a_conflict(self):
        self.assertEqual(preface_findings({'scope': '第二分册设备概况',
                                         'blocks': [{'id': 'preface', 'text': '本册为设备概况分册'}]}), [])

    def advisory(self):
        return {'verdict': 'pass', 'checks': {k: 'pass' for k in CHECKS},
                'evidence_ids': ['real'], 'missing_fact_ids': [], 'unsupported_claims': []}

    def test_model_missing_check_is_held_without_inventing_pass(self):
        record = self.advisory()
        record['checks']['prohabitions_and_permissions'] = record['checks'].pop('prohibitions_and_permissions')
        parsed = validate_advisory(record, {'real'})
        self.assertEqual(parsed['verdict'], 'hold')
        self.assertEqual(parsed['checks']['prohibitions_and_permissions'], 'uncertain')

    def test_model_unknown_citation_is_held(self):
        record = self.advisory()
        parsed = validate_advisory(record, {'different'})
        self.assertEqual(parsed['verdict'], 'hold')
        self.assertEqual(parsed['checks']['source_support'], 'uncertain')

    def test_provider_pass_with_uncertain_permission_is_held(self):
        record = self.advisory()
        record['checks']['prohibitions_and_permissions'] = 'uncertain'
        parsed = validate_advisory(record, {'real'})
        self.assertEqual(parsed['verdict'], 'hold')
        self.assertEqual(parsed['provider_verdict'], 'pass')
        self.assertEqual(parsed['checks']['prohibitions_and_permissions'], 'uncertain')

    def test_provider_pass_cannot_override_its_unsupported_claim(self):
        record = self.advisory()
        record['unsupported_claims'] = ['自行送电许可没有依据']
        parsed = validate_advisory(record, {'real'})
        self.assertEqual(parsed['verdict'], 'hold')
        self.assertEqual(parsed['checks']['source_support'], 'uncertain')

    def test_answer_blind_reuse_rejects_same_id_with_changed_context(self):
        old = [{'id': 'same', 'messages': [{'role': 'user', 'content': '原文A，问题'}]},
               {'id': 'unchanged', 'messages': [{'role': 'user', 'content': '原文B，问题'}]}]
        new = [{'id': 'same', 'messages': [{'role': 'user', 'content': '原文A加安全条件，问题'}]}, old[1]]
        self.assertEqual(matching_prompts(new, old), ['unchanged'])

    def test_answer_blind_reuse_rejects_duplicate_prompt_ids(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            matching_prompts([{'id': 'duplicate'}, {'id': 'duplicate'}], [])


if __name__ == '__main__':
    unittest.main()
