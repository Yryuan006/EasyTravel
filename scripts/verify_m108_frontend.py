"""Run browser regression and supervise explicit formal API process restarts."""
import argparse,json,os,secrets,subprocess,sys,time
from pathlib import Path
import httpx
from verify_m108_backend import load,preserve_evidence


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--database-env',required=True);args=parser.parse_args()
    assert os.environ.get('EASYTRAVEL_ISOLATED_TRANSPORT_PORT')=='55434'
    root=Path(__file__).resolve().parents[1]
    node=root/'.tools/node-m003/node_modules/node/bin/node.exe'
    node_version=subprocess.run([str(node),'--version'],capture_output=True,text=True,check=True).stdout.strip()
    assert node_version=='v24.22.2', 'Use the repository-pinned Node toolchain'
    a,b=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
    request_file=root/'.local'/('m108-restart-'+secrets.token_hex(8)+'.json')
    ack_file=request_file.with_suffix('.ack.json')
    env=os.environ.copy()|load(args.database_env)
    env['PATH']=str(node.parent)+os.pathsep+env['PATH']
    (root/'Docs/M1-08_浏览器工具链.json').write_text(json.dumps(dict(nodeVersion=node_version,nodePath=str(node),python=sys.version.split()[0],workers=1),indent=2),encoding='utf-8')
    env.update(EASYTRAVEL_APP_ENVIRONMENT='test',EASYTRAVEL_APP_LOCAL_BUSINESS_ENABLED='true',
        EASYTRAVEL_APP_LOCAL_IDENTITIES=json.dumps([dict(actor='m104b_browser_a',token=a),dict(actor='m104b_browser_b',token=b)]),
        EASYTRAVEL_BROWSER_TOKEN_A=a,EASYTRAVEL_BROWSER_TOKEN_B=b,EASYTRAVEL_PYTHON=sys.executable,
        EASYTRAVEL_M108_RESTART_REQUEST=str(request_file),EASYTRAVEL_M108_RESTART_ACK=str(ack_file),
        PLAYWRIGHT_BROWSERS_PATH=str(root/'.cache/ms-playwright'))
    subprocess.run([sys.executable,'-m','easytravel.backend.infrastructure.migrate'],env=env,check=True,capture_output=True)
    rules=root/'.local'/('m108-rules-'+secrets.token_hex(8)+'.txt');rules.write_text('confirmation-v1',encoding='utf-8');env['M105B_RULES_FILE']=str(rules)
    handles=[];processes=[];backend=None;browser=None
    def start(name,cmd,cwd):
        handle=(root/f'.local/m108-{name}.log').open('a',encoding='utf-8');handles.append(handle)
        process=subprocess.Popen(cmd,env=env,cwd=cwd,stdout=handle,stderr=subprocess.STDOUT);processes.append(process);return process
    def ready(url,process):
        for _ in range(100):
            if process.poll() is not None:raise RuntimeError('Acceptance server exited')
            try:
                if httpx.get(url,trust_env=False,timeout=1).status_code==200:return
            except httpx.TransportError:pass
            time.sleep(.1)
        raise RuntimeError('Acceptance server readiness timeout')
    only=os.environ.get('EASYTRAVEL_M108_LIFECYCLE_ONLY')=='true'
    try:
        backend=start('backend',[sys.executable,'-m','scripts.m105b_test_server'],root)
        vite=start('vite',[str(node),'node_modules/vite/bin/vite.js','--host','127.0.0.1'],root/'frontend')
        ready('http://127.0.0.1:8000/api/v1/health',backend);ready('http://127.0.0.1:5173/',vite)
        label='Playwright_生命周期针对性' if only else 'Playwright'
        with preserve_evidence(),(root/f'Docs/M1-08_{label}.txt').open('w',encoding='utf-8') as log:
            browser=subprocess.Popen(
                [str(node),'node_modules/@playwright/test/cli.js','test',*(['zz-lifecycle.spec.ts'] if only else [])],
                cwd=root/'frontend',env=env,stdout=log,stderr=subprocess.STDOUT)
            processes.append(browser)
            last_request=None
            while browser.poll() is None:
                if request_file.exists():
                    message=json.loads(request_file.read_text(encoding='utf-8'))
                    if message['request_id']!=last_request:
                        old_pid=backend.pid;backend.terminate();backend.wait(timeout=10)
                        backend=start('formal-backend',[sys.executable,'-m','easytravel.backend.local_server'],root)
                        ready('http://127.0.0.1:8000/api/v1/health',backend)
                        last_request=message['request_id']
                        ack_file.write_text(json.dumps(dict(request_id=last_request,oldPid=old_pid,newPid=backend.pid,formalCompositionRoot=True)),encoding='utf-8')
                if backend.poll() is not None or vite.poll() is not None:raise RuntimeError('Acceptance service unexpectedly exited')
                time.sleep(.05)
            status=browser.returncode
        for path in (root/'Docs').glob('M1-08*'):
            if path.suffix in {'.json','.txt','.md'}:
                content=path.read_text(encoding='utf-8-sig');assert a not in content and b not in content,'Credential found in evidence'
        print('Browser acceptance exit:',status);raise SystemExit(status)
    finally:
        for process in reversed(processes):
            if process.poll() is None:process.terminate();process.wait(timeout=10)
        for handle in handles:handle.close()

if __name__=='__main__':main()
