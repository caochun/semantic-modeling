"""Run document QA against a configured Chat Completions compatible service.

Standard library only. Reads prompts, never references; does not grade answers.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from .paths import ROOT


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def save(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def environment(path):
    """Read literal .env assignments; process environment takes precedence."""
    values = {}
    if path.is_file():
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('export '):
                line = line[7:]
            key, sep, value = line.partition('=')
            if not sep or not key.strip().isidentifier():
                raise ValueError(f'Unsupported .env assignment on line {number}')
            parts = shlex.split(value, comments=True)
            if len(parts) > 1:
                raise ValueError(f'Quote whitespace in .env line {number}')
            values[key.strip()] = parts[0] if parts else ''
    values.update(os.environ)
    return values


def endpoint(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {'http', 'https'} or not parts.hostname:
        raise ValueError('Provide an http(s) API URL')
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('API URL must not contain credentials, query or fragment')
    base = url.rstrip('/')
    if base.endswith('/chat/completions'):
        return base
    return base + ('/v1' if not parts.path.strip('/') else '') + '/chat/completions'


def prepare(args):
    env = environment(args.env_file)
    prefix = {'glm': 'GLM', 'deepseek': 'DEEPSEEK', 'compatible': 'QA'}[args.provider]
    url = args.api_url or env.get(prefix + '_API_URL', '')
    model = args.model or env.get(prefix + '_MODEL', '')
    key_name = args.api_key_env or prefix + '_API_KEY'
    key = env.get(key_name, '')
    if args.provider == 'glm' and not key and not args.api_key_env:
        key_name, key = 'ZHIPU_API_KEY', env.get('ZHIPU_API_KEY', '')
    if args.provider == 'deepseek' and not url:
        url = 'https://api.deepseek.com/v1'
    rows = read_rows(args.prompts)
    if not rows or len({r['id'] for r in rows}) != len(rows):
        raise ValueError('Prompt IDs must be nonempty and unique')
    for row in rows:
        if set(row) != {'id', 'context_id', 'messages'}:
            raise ValueError('Use eval_prompts.jsonl only: unexpected fields')
        if not isinstance(row['id'], str) or not row['id']:
            raise ValueError('Invalid prompt ID')
        if [m.get('role') for m in row['messages']] != ['system', 'user']:
            raise ValueError('Only system/user messages are allowed')
        if any(set(m) != {'role', 'content'} or not isinstance(m['content'], str)
               for m in row['messages']):
            raise ValueError('Invalid prompt messages')
    parameters = {'stream': False, 'max_tokens': args.max_output_tokens}
    if args.temperature is not None:
        parameters['temperature'] = args.temperature
    if args.reasoning_effort:
        parameters['reasoning_effort'] = args.reasoning_effort
    if args.thinking:
        parameters['thinking'] = {'type': args.thinking}
    config = {'provider': args.provider, 'endpoint': endpoint(url) if url else None,
              'model': model or None, 'parameters': parameters, 'timeout_seconds': args.timeout,
              'api_key_env': key_name, 'prompts_sha256': digest(args.prompts.read_bytes()),
              'runner_sha256': digest(Path(__file__).read_bytes()), 'ids': [r['id'] for r in rows],
              'context_window_tokens': args.context_window,
              'input_truncation': False, 'token_count_preflight': 'unavailable',
              'sampling_note': 'Omitted sampling parameters use provider defaults; no determinism claim.'}
    blockers = []
    if not model:
        blockers.append('model_not_configured')
    if not url:
        blockers.append('api_url_not_configured')
    if not key and args.provider != 'compatible':
        blockers.append('api_key_not_configured')
    preflight = {'status': 'blocked_configuration' if blockers else 'ready_for_request',
                 'blockers': blockers, 'configuration': config, 'credential_configured': bool(key),
                 'question_count': len(rows), 'max_input_characters': max(sum(len(m['content']) for m in r['messages']) for r in rows),
                 'max_input_utf8_bytes': max(sum(len(m['content'].encode()) for m in r['messages']) for r in rows),
                 'context_note': 'Characters/bytes are not token counts. Full messages are sent unchanged; server context limits still apply.',
                 'model_evaluation_run': False}
    return rows, config, key, preflight


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(config, key, row):
    payload = dict(config['parameters'], model=config['model'], messages=row['messages'])
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    req = urllib.request.Request(config['endpoint'], json.dumps(payload, ensure_ascii=False).encode(), headers)
    with urllib.request.build_opener(NoRedirect).open(req, timeout=config['timeout_seconds']) as response:
        data = json.load(response)
    choice = data['choices'][0]
    content = choice['message'].get('content')
    finish = choice.get('finish_reason')
    if not isinstance(content, str):
        raise ValueError('Response content is not a string')
    complete = finish == 'stop' and not choice['message'].get('tool_calls')
    # Deliberately omit private reasoning and arbitrary provider response fields.
    return {'status': ('ok' if content else 'empty_response') if complete else 'incomplete',
            'answer': content, 'finish_reason': finish, 'returned_model': data.get('model'),
            'response_id': data.get('id'), 'usage': data.get('usage') or {},
            'system_fingerprint': data.get('system_fingerprint')}


def append(path, event):
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + '\n')
        handle.flush()
        os.fsync(handle.fileno())


def latest_events(path):
    if not path.exists():
        return {}
    # Fail on a damaged final line rather than silently discard a real response.
    return {row['id']: row for row in read_rows(path)}


def http_error(error, key):
    result = {'status': 'http_error', 'http_status': error.code}
    try:
        data = json.loads(error.read())
        detail = data.get('error', data)
        if isinstance(detail, dict):
            for source, target in [('code', 'provider_error_code'), ('message', 'provider_error_message')]:
                value = detail.get(source)
                if isinstance(value, (str, int)):
                    value = str(value)
                    if key:
                        value = value.replace(key, '[redacted]')
                    value = re.sub(r'(?i)bearer\s+\S+', 'Bearer [redacted]', value)
                    result[target] = value[:500]
    except (ValueError, OSError, AttributeError):
        pass
    finally:
        error.close()
    return result


def export(out, rows):
    latest = latest_events(out / 'attempts.jsonl')
    answers, counts = [], {}
    for row in rows:
        event = latest.get(row['id'], {'status': 'not_attempted'})
        status = event['status']
        counts[status] = counts.get(status, 0) + 1
        if status in {'ok', 'empty_response'}:
            answers.append({'id': row['id'], 'answer': event['answer']})
    temp = out / 'answers.jsonl.tmp'
    temp.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in answers), encoding='utf-8')
    temp.replace(out / 'answers.jsonl')
    responses = sum(counts.get(s, 0) for s in ['ok', 'empty_response', 'incomplete'])
    report = {'expected': len(rows), 'received_complete': len(answers), 'statuses': counts,
              'requests_attempted': len(latest), 'model_responses_received': responses,
              'model_evaluation_run': responses > 0, 'scoring_status': 'not_graded',
              'full_run_score': None, 'notes': 'Incomplete/error attempts remain in attempts.jsonl; no semantic grading.'}
    save(out / 'status.json', report)
    return report


def run(args, rows, config, key):
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    lock = out / '.running'
    with lock.open('x') as handle:
        handle.write(str(os.getpid()))
    try:
        manifest = out / 'run.json'
        if manifest.exists():
            if not args.resume:
                raise ValueError('Run exists; choose a new directory or use --resume')
            previous = json.loads(manifest.read_text(encoding='utf-8'))
            if previous['configuration'] != config:
                raise ValueError('Run configuration/input changed; use a new directory')
        else:
            if any(p != lock for p in out.iterdir()):
                raise ValueError('Output directory must be empty for a new run')
            save(manifest, {'created_at': stamp(), 'configuration': config})
            (out / 'prompts.jsonl').write_bytes(args.prompts.read_bytes())
        latest = latest_events(out / 'attempts.jsonl')
        attempted = 0
        try:
            for row in rows:
                prior = latest.get(row['id'])
                if prior and (prior['status'] in {'ok', 'empty_response'} or not args.retry_failed):
                    continue
                if args.limit is not None and attempted >= args.limit:
                    break
                event = {'id': row['id'], 'started_at': stamp(), 'status': 'started'}
                append(out / 'attempts.jsonl', event)
                started = time.monotonic()
                try:
                    result = request(config, key, row)
                except urllib.error.HTTPError as error:
                    result = http_error(error, key)
                except (urllib.error.URLError, TimeoutError, OSError) as error:
                    result = {'status': 'connection_error', 'error_type': type(error).__name__}
                except (ValueError, KeyError, IndexError, TypeError) as error:
                    result = {'status': 'invalid_response', 'error_type': type(error).__name__}
                event.update(result, finished_at=stamp(), elapsed_seconds=round(time.monotonic() - started, 3))
                append(out / 'attempts.jsonl', event)
                attempted += 1
                print(json.dumps({'id': row['id'], 'status': event['status'], 'attempted_this_session': attempted}), flush=True)
                export(out, rows)
                if event['status'] not in {'ok', 'empty_response'}:
                    break  # Resolve provider/configuration trouble before sending more documents.
        finally:
            report = export(out, rows)
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report['received_complete'] == len(rows) else 2
    finally:
        lock.unlink()


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prompts', type=Path, default=ROOT / '.semantic/qa_release_20261007/eval/eval_prompts.jsonl')
    p.add_argument('--env-file', type=Path, default=ROOT / '.env')
    p.add_argument('--provider', choices=['glm', 'deepseek', 'compatible'], default='glm')
    p.add_argument('--api-url')
    p.add_argument('--api-key-env')
    p.add_argument('--model')
    p.add_argument('--max-output-tokens', type=int, default=8192)
    p.add_argument('--temperature', type=float)
    p.add_argument('--reasoning-effort', choices=['low', 'high', 'max'])
    p.add_argument('--thinking', choices=['enabled', 'disabled'])
    p.add_argument('--context-window', type=int)
    p.add_argument('--timeout', type=float, default=180)
    p.add_argument('--output', type=Path)
    p.add_argument('--preflight', type=Path, help='Write configuration check; do not call service')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--retry-failed', action='store_true', help='Explicitly retry unresolved/failed attempts')
    p.add_argument('--limit', type=int, help='Maximum new requests this session; full input remains frozen')
    return p


def main():
    p = parser()
    args = p.parse_args()
    if args.max_output_tokens <= 0 or args.timeout <= 0 or (args.limit is not None and args.limit <= 0):
        p.error('Token limit, timeout and request limit must be positive')
    if args.retry_failed and not args.resume:
        p.error('--retry-failed requires --resume')
    rows, config, key, preflight = prepare(args)
    if args.preflight:
        args.preflight.parent.mkdir(parents=True, exist_ok=True)
        save(args.preflight, preflight)
        print(json.dumps(preflight, ensure_ascii=False, indent=2))
        return 0
    if preflight['blockers']:
        raise ValueError('Missing configuration: ' + ', '.join(preflight['blockers']))
    if not args.output:
        p.error('--output is required for a real run')
    return run(args, rows, config, key)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, FileExistsError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
