"""M1-04b real browser verification; fresh dedicated DB, no resets or deletes.

Prepare a NEW .local env with prepare_m002_test_db.py first. Credentials are
ephemeral process environment values, never VITE variables, URLs or logs.
"""
from m104b_evidence import preserve_accepted_evidence
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
        EASYTRAVEL_APP_LOCAL_IDENTITIES=json.dumps([{'actor': 'm104b_browser_a', 'token': token_a}, {'actor': 'm104b_browser_b', 'token': token_b}]),
        EASYTRAVEL_BROWSER_TOKEN_A=token_a, EASYTRAVEL_BROWSER_TOKEN_B=token_b)
    env['EASYTRAVEL_PYTHON']=sys.executable
    frontend = ROOT / 'frontend'
    processes = []
    handles = []
    try:
        for name, command, cwd in [
            ('backend', [sys.executable, '-m', 'easytravel.backend.local_server'], ROOT),
            ('vite', ['node', 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1'], frontend),
        ]:
            handle = (ROOT / f'.local/m104b-{name}.log').open('w', encoding='utf-8'); handles.append(handle)
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
        assert actual == json.loads((ROOT / 'Docs/M1-04b_openapi.json').read_text(encoding='utf-8'))
        (ROOT / 'Docs/M1-04b_openapi.json').write_text(json.dumps(actual, ensure_ascii=False, indent=2), encoding='utf-8')
        with preserve_accepted_evidence(), (ROOT / 'Docs/M1-04b_Playwright.txt').open('w', encoding='utf-8') as log:
            result = subprocess.run(['node', 'node_modules/@playwright/test/cli.js', 'test'], cwd=frontend, env=env, stdout=log, stderr=subprocess.STDOUT)
        print('Playwright exit:', result.returncode, flush=True)
        if result.returncode:
            raise SystemExit(result.returncode)
        import psycopg
        from psycopg.rows import dict_row
        evidence=json.loads((ROOT/'Docs/M1-04b_HTTP证据.json').read_text(encoding='utf-8'))
        with psycopg.connect(env['EASYTRAVEL_DATABASE_URL'],row_factory=dict_row) as conn:
            data=[]
            for case in evidence['cases']:
                tid=case['tripId']
                versions=conn.execute('SELECT trip_id,owner_id,revision,payload FROM easytravel.draft_versions WHERE trip_id=%s',(tid,)).fetchall()
                contexts=conn.execute('SELECT working_revision,intake_revision,run_id FROM easytravel.draft_contexts WHERE trip_id=%s',(tid,)).fetchall()
                runs=conn.execute('SELECT run_id,status,result,error,diagnostics_budget FROM easytravel.generation_runs WHERE trip_id=%s ORDER BY created_at',(tid,)).fetchall()
                commands=conn.execute("SELECT idempotency_key,response_status,result FROM easytravel.api_commands WHERE trip_id=%s AND path LIKE '%%/generations'",(tid,)).fetchall()
                if case.get('refresh'):
                    assert len(versions)==len(contexts)==1
                    assert versions[0]['payload']==case['refresh']['trip']['working']['payload']
                    assert str(contexts[0]['run_id'])==case['refresh']['state']['source_run']['run_id']
                    assert runs[-1]['diagnostics_budget']==case['refresh']['state']['source_run']['diagnostics_budget']
                else:
                    assert not versions and not contexts
                data.append(dict(tripId=tid,versions=versions,contexts=contexts,runs=runs,commands=commands))
            database=conn.execute('SELECT current_database() name').fetchone()['name']
        (ROOT/'Docs/M1-04b_PostgreSQL证据.json').write_text(json.dumps(dict(database=database,cases=data,refreshMatchesPostgreSQL=True),default=str,ensure_ascii=False,indent=2),encoding='utf-8')
        for evidence_file in (ROOT / 'Docs').glob('M1-04b*'):
            if evidence_file.suffix in ('.txt', '.json'):
                content = evidence_file.read_text(encoding='utf-8-sig')
                assert token_a not in content and token_b not in content, 'Token found in evidence'
        print('Browser -> FastAPI -> separate Mock Worker -> PostgreSQL -> browser and refresh budget verified; resources retained.',flush=True)
    finally:
        for process in reversed(processes):
            process.terminate()
            process.wait(timeout=10)
        for handle in handles:
            handle.close()


if __name__ == '__main__':
    main()
