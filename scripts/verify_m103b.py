"""M1-03b real browser verification; fresh dedicated DB, no resets or deletes.

Prepare a NEW .local env with prepare_m002_test_db.py first. Credentials are
ephemeral process environment values, never VITE variables, URLs or logs.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]


def env_file(path):
    return dict(line.split('=', 1) for line in (ROOT / path).read_text(encoding='utf-8-sig').splitlines() if line and not line.startswith('#'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database-env', required=True)
    args = parser.parse_args()
    env = os.environ.copy() | env_file(args.database_env)
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    env.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.cache/ms-playwright'))
    subprocess.run([sys.executable, '-m', 'easytravel.backend.infrastructure.migrate'], cwd=ROOT, env=env, check=True)
    token_a, token_b = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    env.update(EASYTRAVEL_APP_ENVIRONMENT='test', EASYTRAVEL_APP_LOCAL_BUSINESS_ENABLED='true',
        EASYTRAVEL_APP_LOCAL_IDENTITIES=json.dumps([{'actor': 'm103b_browser_a', 'token': token_a}, {'actor': 'm103b_browser_b', 'token': token_b}]),
        EASYTRAVEL_BROWSER_TOKEN_A=token_a, EASYTRAVEL_BROWSER_TOKEN_B=token_b)
    frontend = ROOT / 'frontend'
    processes = []
    handles = []
    try:
        for name, command, cwd in [
            ('backend', [sys.executable, '-m', 'easytravel.backend.local_server'], ROOT),
            ('vite', ['node', 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1'], frontend),
        ]:
            handle = (ROOT / f'.local/m103b-{name}.log').open('w', encoding='utf-8'); handles.append(handle)
            processes.append(subprocess.Popen(command, cwd=cwd, env=env, stdout=handle, stderr=subprocess.STDOUT))
        opener = build_opener(ProxyHandler({}))
        for url in ['http://127.0.0.1:8000/api/v1/health', 'http://127.0.0.1:5173/']:
            for _ in range(100):
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError('Test server exited; inspect local logs (credentials omitted).')
                try:
                    with opener.open(url, timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError('Test server readiness timed out.')
        with opener.open('http://127.0.0.1:8000/openapi.json', timeout=5) as response:
            actual = json.load(response)
        assert actual == json.loads((ROOT / 'Docs/M1-03a_openapi.json').read_text(encoding='utf-8'))
        (ROOT / 'Docs/M1-03b_openapi.json').write_text(json.dumps(actual, ensure_ascii=False, indent=2), encoding='utf-8')
        with (ROOT / 'Docs/M1-03b_Playwright.txt').open('w', encoding='utf-8') as log:
            result = subprocess.run(['node', 'node_modules/@playwright/test/cli.js', 'test'], cwd=frontend, env=env, stdout=log, stderr=subprocess.STDOUT)
        print('Playwright exit:', result.returncode, flush=True)
        if result.returncode:
            raise SystemExit(result.returncode)
        import psycopg
        from psycopg.rows import dict_row
        evidence = json.loads((ROOT / 'Docs/M1-03b_HTTP证据.json').read_text(encoding='utf-8'))
        final = evidence['refresh']['response']
        with psycopg.connect(env['EASYTRAVEL_DATABASE_URL'], row_factory=dict_row) as conn:
            rows = conn.execute('SELECT trip_id,owner_id,revision,input FROM easytravel.trip_intake_versions WHERE trip_id=%s ORDER BY revision', (evidence['tripId'],)).fetchall()
            assert [r['revision'] for r in rows] == [1,2,3]
            for row, step in zip(rows, ['create', 'answer', 'update']):
                assert row['input'] == evidence[step]['response']['intake']['input']
                assert row['owner_id'] == 'm103b_browser_a'
            assert rows[-1]['input'] == final['intake']['input']
            receipts = conn.execute('SELECT method,path,idempotency_key,response_status,result FROM easytravel.api_commands WHERE trip_id=%s ORDER BY response_status DESC,path', (evidence['tripId'],)).fetchall()
            assert len(receipts) == 3
            for step in ['answer','update']:
                assert any(str(r['idempotency_key']) == evidence[step]['key'] and r['result'] == evidence[step]['response'] for r in receipts)
            draft_count = conn.execute('SELECT count(*) n FROM easytravel.working_drafts WHERE trip_id=%s', (evidence['tripId'],)).fetchone()['n']
            assert draft_count == 0
            database = conn.execute('SELECT current_database() name').fetchone()['name']
        (ROOT / 'Docs/M1-03b_PostgreSQL证据.json').write_text(json.dumps({'database': database, 'rows': rows, 'receipts': receipts, 'workingDraftCount': draft_count, 'refreshEqualsPersistedVersion3': True}, default=str, ensure_ascii=False, indent=2), encoding='utf-8')
        for evidence_file in (ROOT / 'Docs').glob('M1-03b*'):
            if evidence_file.suffix in ('.txt', '.json'):
                content = evidence_file.read_text(encoding='utf-8-sig')
                assert token_a not in content and token_b not in content, 'Token found in evidence'
        print('Browser HTTP create/answer/update/refresh and independent PostgreSQL versions/receipts verified; resources retained.', flush=True)
    finally:
        for process in reversed(processes):
            process.terminate()
            process.wait(timeout=10)
        for handle in handles:
            handle.close()


if __name__ == '__main__':
    main()
