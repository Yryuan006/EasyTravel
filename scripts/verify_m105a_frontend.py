"""Existing browser regression against a fresh isolated formal database."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import httpx
from verify_m105a_backend import load, preserve_evidence

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--database-env',required=True);args=parser.parse_args()
    assert os.environ.get('EASYTRAVEL_ISOLATED_TRANSPORT_PORT')=='55434'
    root=Path(__file__).resolve().parents[1]
    a,b=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
    env=os.environ.copy() | load(args.database_env)
    env.update(EASYTRAVEL_APP_ENVIRONMENT='test',EASYTRAVEL_APP_LOCAL_BUSINESS_ENABLED='true',
        EASYTRAVEL_APP_LOCAL_IDENTITIES=json.dumps([dict(actor='m104b_browser_a',token=a),dict(actor='m104b_browser_b',token=b)]),
        EASYTRAVEL_BROWSER_TOKEN_A=a,EASYTRAVEL_BROWSER_TOKEN_B=b,EASYTRAVEL_PYTHON=sys.executable,
        PLAYWRIGHT_BROWSERS_PATH=str(root/'.cache/ms-playwright'))
    subprocess.run([sys.executable,'-m','easytravel.backend.infrastructure.migrate'],env=env,check=True,capture_output=True)
    handles=[]; processes=[]
    try:
        for name,cmd,cwd in [('backend',[sys.executable,'-m','easytravel.backend.local_server'],root),
                             ('vite',['node','node_modules/vite/bin/vite.js','--host','127.0.0.1'],root/'frontend')]:
            handle=(root/f'.local/m105a-compat-{name}.log').open('w',encoding='utf-8');handles.append(handle)
            processes.append(subprocess.Popen(cmd,env=env,cwd=cwd,stdout=handle,stderr=subprocess.STDOUT))
        for url in ['http://127.0.0.1:8000/api/v1/health','http://127.0.0.1:5173/']:
            for _ in range(100):
                if any(p.poll() is not None for p in processes):raise RuntimeError('Compatibility server exited')
                try:
                    if httpx.get(url,trust_env=False,timeout=1).status_code==200:break
                except httpx.TransportError:pass
                time.sleep(.1)
            else:raise RuntimeError('Compatibility readiness timeout')
        with preserve_evidence(), (root/'Docs/M1-05a_Playwright.txt').open('w',encoding='utf-8') as log:
            result=subprocess.run(['node','node_modules/@playwright/test/cli.js','test'],cwd=root/'frontend',env=env,stdout=log,stderr=subprocess.STDOUT)
            for pattern in ('M1-03b_HTTP证据.json','M1-03b_创建回归_浏览器HTTP证据.json','M1-04b_HTTP证据.json'):
                source=root/'Docs'/pattern
                if source.exists():
                    (root/'Docs'/('M1-05a_兼容_'+pattern)).write_bytes(source.read_bytes())
            print('Existing browser tests exit:',result.returncode)
        raise SystemExit(result.returncode)
    finally:
        for process in reversed(processes):
            process.terminate();process.wait(timeout=10)
        for handle in handles:handle.close()

if __name__=='__main__':main()
