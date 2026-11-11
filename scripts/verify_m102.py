"""M1-02 real browser verification; fresh dedicated DB, no resets or deletes.

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
    parser.add_argument('--backend-regression-env')
    parser.add_argument('--backend-api-env')
    args = parser.parse_args()
    env = os.environ.copy() | env_file(args.database_env)
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    env.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.cache/ms-playwright'))
    if args.backend_regression_env and args.backend_api_env:
        backend_env = env | env_file('.env') | env_file(args.backend_regression_env)
        backend_env['EASYTRAVEL_M101_TEST_DATABASE_URL'] = env_file(args.backend_api_env)['EASYTRAVEL_DATABASE_URL']
        backend_env['EASYTRAVEL_M101_RETAINED_DATABASE_URL'] = env_file('.local/m002-final.env')['EASYTRAVEL_DATABASE_URL']
        with (ROOT / 'Docs/M1-02_后端全量回归.txt').open('w', encoding='utf-8') as log:
            result = subprocess.run([sys.executable, '-m', 'pytest', '-v', '-s'], cwd=ROOT, env=backend_env, stdout=log, stderr=subprocess.STDOUT)
        print('Backend full regression exit:', result.returncode, flush=True)
        if result.returncode:
            raise SystemExit(result.returncode)
    subprocess.run([sys.executable, '-m', 'easytravel.backend.infrastructure.migrate'], cwd=ROOT, env=env, check=True)
    token_a, token_b = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    env.update(EASYTRAVEL_APP_ENVIRONMENT='test', EASYTRAVEL_APP_LOCAL_BUSINESS_ENABLED='true',
        EASYTRAVEL_APP_LOCAL_IDENTITIES=json.dumps([{'actor': 'm102_browser_a', 'token': token_a}, {'actor': 'm102_browser_b', 'token': token_b}]),
        EASYTRAVEL_BROWSER_TOKEN_A=token_a, EASYTRAVEL_BROWSER_TOKEN_B=token_b)
    frontend = ROOT / 'frontend'
    processes = []
    handles = []
    try:
        for name, command, cwd in [
            ('backend', [sys.executable, '-m', 'easytravel.backend.local_server'], ROOT),
            ('vite', ['node', 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1'], frontend),
        ]:
            handle = (ROOT / f'.local/m102-{name}.log').open('w', encoding='utf-8'); handles.append(handle)
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
        with (ROOT / 'Docs/M1-02_Playwright.txt').open('w', encoding='utf-8') as log:
            result = subprocess.run(['node', 'node_modules/@playwright/test/cli.js', 'test'], cwd=frontend, env=env, stdout=log, stderr=subprocess.STDOUT)
        print('Playwright exit:', result.returncode, flush=True)
        if result.returncode:
            raise SystemExit(result.returncode)
        # Independent SQL read proves the exact browser Trip and immutable input are persisted.
        import psycopg
        from psycopg.rows import dict_row
        evidence = json.loads((ROOT / 'Docs/M1-02_浏览器HTTP证据.json').read_text(encoding='utf-8'))
        trip = evidence['post']['response']
        with psycopg.connect(env['EASYTRAVEL_DATABASE_URL'], row_factory=dict_row) as conn:
            rows = conn.execute('SELECT trip_id, owner_id, revision, input FROM easytravel.trip_intake_versions WHERE trip_id = %s', (trip['trip_id'],)).fetchall()
            assert len(rows) == 1 and rows[0]['input'] == trip['intake']['input']
            assert rows[0]['owner_id'] == 'm102_browser_a' and rows[0]['revision'] == trip['intake']['intake_revision']
            receipt = conn.execute('SELECT idempotency_key, response_status, result FROM easytravel.api_commands WHERE trip_id = %s', (trip['trip_id'],)).fetchall()
            assert len(receipt) == 1 and str(receipt[0]['idempotency_key']) == evidence['post']['key'] and receipt[0]['result'] == trip
            draft_count = conn.execute('SELECT count(*) AS n FROM easytravel.working_drafts WHERE trip_id = %s', (trip['trip_id'],)).fetchone()['n']
            assert draft_count == 0
            database = conn.execute('SELECT current_database() AS name').fetchone()['name']
        (ROOT / 'Docs/M1-02_PostgreSQL证据.json').write_text(json.dumps({'database': database, 'rows': rows, 'receipt': receipt, 'workingDraftCount': draft_count, 'exactBrowserInputMatch': True}, default=str, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Browser HTTP / refresh / independent PostgreSQL evidence verified; resources retained.', flush=True)
    finally:
        for process in reversed(processes):
            process.terminate()
            process.wait(timeout=10)
        for handle in handles:
            handle.close()


if __name__ == '__main__':
    main()
