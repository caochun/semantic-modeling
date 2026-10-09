"""Offline fixtures: these tests do not run or grade a real model."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from qa_pipeline import baseline as qa


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.prompts = self.base / 'prompts.jsonl'
        self.rows = [{'id': str(i), 'context_id': 'full', 'messages': [
            {'role': 'system', 'content': '依据资料'},
            {'role': 'user', 'content': '完整上下文\n' * 500 + f'问题{i}'}]} for i in range(3)]
        self.prompts.write_text(''.join(json.dumps(r) + '\n' for r in self.rows))
        self.args = qa.parser().parse_args([
            '--provider', 'compatible', '--model', 'fixture-model',
            '--api-url', 'http://localhost:1234/v1', '--prompts', str(self.prompts),
            '--env-file', str(self.base / '.env'), '--output', str(self.base / 'run')])
        self.rows, self.config, self.key, self.preflight = qa.prepare(self.args)

    def run_fixture(self, response):
        with patch.object(qa, 'request', side_effect=response) as call, contextlib.redirect_stdout(io.StringIO()):
            result = qa.run(self.args, self.rows, self.config, self.key)
        return result, call

    def test_preflight_does_not_request_and_contains_no_secret(self):
        self.args.env_file.write_text('QA_API_KEY="fake-secret"\n')
        with patch.dict(qa.os.environ, {}, clear=True), patch.object(qa, 'request') as call:
            _, _, key, result = qa.prepare(self.args)
        self.assertEqual(key, 'fake-secret')
        self.assertNotIn('fake-secret', json.dumps(result))
        self.assertEqual(result['question_count'], 3)
        call.assert_not_called()

    def test_reject_answer_bearing_input_and_duplicate_ids(self):
        for rows in [[dict(self.rows[0], answer='leak')], [self.rows[0]] * 2]:
            self.prompts.write_text(''.join(json.dumps(r) + '\n' for r in rows))
            with self.assertRaises(ValueError):
                qa.prepare(self.args)

    def test_partial_run_resume_preserves_full_prompts_and_empty_answer(self):
        self.args.limit = 1
        result, calls = self.run_fixture([{'status': 'ok', 'answer': 'answer'}])
        self.assertEqual(result, 2)
        self.assertEqual(calls.call_args.args[2]['messages'], self.rows[0]['messages'])
        self.args.limit = None
        self.args.resume = True
        result, calls = self.run_fixture([{'status': 'empty_response', 'answer': ''}, {'status': 'ok', 'answer': 'last'}])
        self.assertEqual(result, 0)
        self.assertEqual(calls.call_count, 2)
        answers = qa.read_rows(self.args.output / 'answers.jsonl')
        self.assertEqual([r['answer'] for r in answers], ['answer', '', 'last'])
        report = json.loads((self.args.output / 'status.json').read_text())
        self.assertIsNone(report['full_run_score'])

    def test_truncation_retained_not_exported_and_no_implicit_retry(self):
        result, calls = self.run_fixture([{'status': 'incomplete', 'answer': 'partial', 'finish_reason': 'length'}])
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(qa.read_rows(self.args.output / 'answers.jsonl'), [])
        self.assertEqual(qa.latest_events(self.args.output / 'attempts.jsonl')['0']['answer'], 'partial')
        self.args.resume = True
        result, calls = self.run_fixture([{'status': 'ok', 'answer': 'a'}, {'status': 'ok', 'answer': 'b'}])
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(result, 2)
        self.args.retry_failed = True
        result, calls = self.run_fixture([{'status': 'ok', 'answer': 'retry'}])
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(result, 0)

    def test_changed_config_and_existing_run_rejected(self):
        self.args.limit = 1
        self.run_fixture([{'status': 'ok', 'answer': 'a'}])
        with self.assertRaises(ValueError):
            self.run_fixture([])
        self.args.resume = True
        self.config['model'] = 'different'
        with self.assertRaises(ValueError):
            self.run_fixture([])

    def test_interrupt_journal_requires_explicit_retry(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_fixture(KeyboardInterrupt())
        self.assertFalse((self.args.output / '.running').exists())
        self.assertEqual(qa.latest_events(self.args.output / 'attempts.jsonl')['0']['status'], 'started')
        self.args.resume = True
        self.args.limit = 1
        _, call = self.run_fixture([{'status': 'ok', 'answer': 'next'}])
        self.assertEqual(call.call_args.args[2]['id'], '1')

    def test_http_payload_and_response_metadata(self):
        data = {'model': 'returned-version', 'choices': [{'finish_reason': 'stop', 'message': {
            'content': 'answer', 'reasoning_content': 'private-reasoning'}}], 'usage': {'prompt_tokens': 42}}
        with patch.object(qa.urllib.request, 'build_opener') as opener:
            opener.return_value.open.return_value = contextlib.closing(io.BytesIO(json.dumps(data).encode()))
            response = qa.request(self.config, 'fake-key', self.rows[0])
            request = opener.return_value.open.call_args.args[0]
        sent = json.loads(request.data)
        self.assertEqual(sent['messages'], self.rows[0]['messages'])
        self.assertEqual(response['returned_model'], 'returned-version')
        self.assertEqual(response['usage']['prompt_tokens'], 42)
        self.assertNotIn('private-reasoning', json.dumps(response))

    def test_endpoint_credentials_redirect_and_env_literals(self):
        self.assertEqual(qa.endpoint('https://example.test'), 'https://example.test/v1/chat/completions')
        with self.assertRaises(ValueError):
            qa.endpoint('https://example.test?api_key=secret')
        self.assertIsNone(qa.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.test'))
        self.args.env_file.write_text('export QA_API_KEY="literal$KEY" # comment\n')
        with patch.dict(qa.os.environ, {}, clear=True):
            self.assertEqual(qa.environment(self.args.env_file)['QA_API_KEY'], 'literal$KEY')

    def test_provider_error_redaction_and_no_false_model_result(self):
        body = {'error': {'code': '1113', 'message': 'quota fake-key Bearer other-secret'}}
        error = qa.urllib.error.HTTPError('https://example.test', 429, 'rate limit', {}, io.BytesIO(json.dumps(body).encode()))
        result = qa.http_error(error, 'fake-key')
        self.assertEqual(result['provider_error_code'], '1113')
        self.assertNotIn('fake-key', result['provider_error_message'])
        self.assertNotIn('other-secret', result['provider_error_message'])
        self.run_fixture([result])
        report = json.loads((self.args.output / 'status.json').read_text())
        self.assertFalse(report['model_evaluation_run'])
        self.assertEqual(report['requests_attempted'], 1)


if __name__ == '__main__':
    unittest.main()
