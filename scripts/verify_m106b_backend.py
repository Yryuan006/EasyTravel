"""Run all backend tests only with resources supplied by the isolated provisioner."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys

def load(path):
    return dict(l.split('=', 1) for l in Path(path).read_text(encoding='utf-8-sig').splitlines() if l and not l.startswith('#'))

@contextmanager
def preserve_evidence():
    saved = {p: p.read_bytes() for p in Path('Docs').glob('M1-*') if p.is_file() and not p.name.startswith('M1-06b')}
    try:
        yield
    finally:
        for path, data in saved.items():
            if path.read_bytes() != data:
                path.write_bytes(data)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database-prefix', required=True)
    parser.add_argument('--experiment-env', required=True)
    parser.add_argument('--retained-env', required=True)
    parser.add_argument('--browser-only', action='store_true')
    args = parser.parse_args()
    assert os.environ.get('EASYTRAVEL_ISOLATED_TRANSPORT_PORT') == '55434'
    env = os.environ.copy() | load(args.experiment_env) | load(args.database_prefix+'-regression.env')
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    for key, kind in [('M101','api'),('M103','compat'),('M104','gen'),('M105','confirm')]:
        env[f'EASYTRAVEL_{key}_TEST_DATABASE_URL'] = load(args.database_prefix+f'-{kind}.env')['EASYTRAVEL_DATABASE_URL']
    env['EASYTRAVEL_M107_TEST_DATABASE_URL'] = load(args.database_prefix+'-confirm.env')['EASYTRAVEL_DATABASE_URL']
    env['EASYTRAVEL_M106_TEST_DATABASE_URL'] = load(args.database_prefix+'-confirm.env')['EASYTRAVEL_DATABASE_URL']
    env['EASYTRAVEL_M101_RETAINED_DATABASE_URL'] = load(args.retained_env)['EASYTRAVEL_DATABASE_URL']
    statuses = []
    with preserve_evidence():
        suites=[] if args.browser_only else [('后端针对性修改测试',['tests/backend/test_replanning.py']), ('后端全量回归',[])]
        for label, tests in suites:
            with Path(f'Docs/M1-06b_{label}.txt').open('w', encoding='utf-8') as stream:
                result = subprocess.run([sys.executable, '-m','pytest','-v','-s',*tests],env=env,stdout=stream,stderr=subprocess.STDOUT)
            statuses.append(result.returncode)
            print(label, result.returncode)
            print('\n'.join(Path(f'Docs/M1-06b_{label}.txt').read_text(encoding='utf-8').splitlines()[-12:]))
        if not any(statuses):
            r = subprocess.run([sys.executable,'scripts/verify_m106b_frontend.py','--database-env',args.database_prefix+'-frontend.env'],env=env)
            statuses.append(r.returncode)
    raise SystemExit(max(statuses))

if __name__ == '__main__':
    main()
