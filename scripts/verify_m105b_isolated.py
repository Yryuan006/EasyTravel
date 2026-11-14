"""Full legacy/formal regression on a NEW cluster at an independent port.

The unchanged legacy guards require nominal localhost:55432. A test-process-only
sitecustomize maps psycopg transport to 55434, including child processes. The
original ENV-00 stays running; all old table fingerprints are read-only checked.
No existing database/role/schema/Checkpoint is written, reset or removed.
"""
import argparse, json, os, secrets, subprocess, sys, time
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg import sql
ROOT=Path(__file__).resolve().parents[1]

def docker(*args,input=None):
    r=subprocess.run(['docker',*args],input=input,capture_output=True,text=True,encoding='utf-8')
    if r.returncode: raise RuntimeError('Dedicated Docker operation failed: '+args[0])
    return r.stdout.strip()
def load(path):
    return dict(l.split('=',1) for l in Path(path).read_text(encoding='utf-8-sig').splitlines() if l and not l.startswith('#'))
def fingerprint():
    with psycopg.connect(load('.env')['EASYTRAVEL_TEST_DATABASE_URL']) as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        rows=conn.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2").fetchall()
        return {s+'.'+t:conn.execute(sql.SQL("SELECT count(*),md5(COALESCE(string_agg(h,'' ORDER BY h),'')) FROM (SELECT md5(row_to_json(t)::text) h FROM {}.{} t)x").format(sql.Identifier(s),sql.Identifier(t))).fetchone() for s,t in rows}

SHIM='''import os
if os.environ.get('EASYTRAVEL_ISOLATED_TRANSPORT_PORT'):
 import psycopg
 from psycopg.conninfo import conninfo_to_dict,make_conninfo
 original_sync=psycopg.Connection.connect
 original_async=psycopg.AsyncConnection.connect
 def mapped(dsn,kwargs):
  c=conninfo_to_dict(dsn,**{k:v for k,v in kwargs.items() if k in ('host','port','dbname','user','password')})
  if c.get('port')=='55432' and c.get('host') in ('127.0.0.1','localhost','::1'):
   kwargs=kwargs|dict(port=os.environ['EASYTRAVEL_ISOLATED_TRANSPORT_PORT'])
  return dsn,kwargs
 def connect(cls,conninfo='',**kwargs):
  dsn,kwargs=mapped(conninfo,kwargs)
  return original_sync(dsn,**kwargs)
 async def async_connect(cls,conninfo='',**kwargs):
  dsn,kwargs=mapped(conninfo,kwargs)
  return await original_async(dsn,**kwargs)
 psycopg.Connection.connect=classmethod(connect)
 psycopg.AsyncConnection.connect=classmethod(async_connect)
 psycopg.connect=psycopg.Connection.connect
'''

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--browser-only',action='store_true')
    args=parser.parse_args()
    info=json.loads(docker('inspect','easytravel-env00-postgres'))[0]
    assert info['Config']['Labels']['com.easytravel.scope']=='env00' and info['State']['Running']
    before=fingerprint()
    suffix=uuid4().hex[:10];name='easytravel-m105b-isolated-'+suffix
    secret=ROOT/'.local'/('m105b-admin-'+suffix);secret.write_text(secrets.token_hex(32),encoding='utf-8')
    experiment_pass=secrets.token_hex(32)
    prefix='.local/m105b-isolated-'+suffix
    experiment=prefix+'-experiment.env'
    Path(experiment).write_text(f'EASYTRAVEL_TEST_DATABASE_URL=postgresql://easytravel_env00:{experiment_pass}@127.0.0.1:55432/easytravel_env00_test\nEASYTRAVEL_MODEL_MODE=mock\nLANGGRAPH_STRICT_MSGPACK=true\n',encoding='utf-8')
    shim=ROOT/'.local'/('m105b-transport-'+suffix);shim.mkdir();(shim/'sitecustomize.py').write_text(SHIM,encoding='utf-8')
    docker('run','-d','--name',name,'--label','com.easytravel.scope=m105b-isolated','-p','127.0.0.1:55434:5432',
        '-e','POSTGRES_USER=easytravel_env00_admin','-e','POSTGRES_PASSWORD_FILE=/run/secrets/admin',
        '--mount',f'type=bind,source={secret},target=/run/secrets/admin,readonly',
        '--mount',f'type=volume,source={name}-data,target=/var/lib/postgresql/data',info['Image'])
    pth=Path(sys.prefix)/'Lib/site-packages/easytravel_m105b_test_transport.pth'
    assert not pth.exists()
    pth.write_text(str(shim)+'\n',encoding='utf-8')
    try:
        isolated=json.loads(docker('inspect',name))[0]
        assert isolated['Config']['Labels']['com.easytravel.scope']=='m105b-isolated'
        assert isolated['NetworkSettings']['Ports']['5432/tcp']==[dict(HostIp='127.0.0.1',HostPort='55434')]
        assert any(m.get('Name')==name+'-data' for m in isolated['Mounts'])
        for _ in range(100):
            try:
                with psycopg.connect(host='127.0.0.1',port=55434,user='easytravel_env00_admin',password=secret.read_text(),dbname='postgres',connect_timeout=1) as c:
                    c.execute('SELECT 1')
                break
            except psycopg.OperationalError:time.sleep(.2)
        else:raise RuntimeError('Isolated PostgreSQL did not start')
        statements=f"CREATE ROLE easytravel_env00 LOGIN PASSWORD '{experiment_pass}' NOSUPERUSER NOCREATEDB NOCREATEROLE;\nCREATE DATABASE easytravel_env00_test OWNER easytravel_env00;\nREVOKE CONNECT ON DATABASE easytravel_env00_test FROM PUBLIC;\n"
        targets=[]
        for kind in ['api','regression','compat','gen','retained','confirm','browser','frontend']:
            role='easytravel_m002_'+uuid4().hex[:12];db='easytravel_m002_test_'+uuid4().hex[:12];password=secrets.token_hex(32)
            statements+=f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE;\nCREATE DATABASE {db} OWNER {role};\nREVOKE CONNECT ON DATABASE {db} FROM PUBLIC;\n"
            Path(prefix+'-'+kind+'.env').write_text(f'EASYTRAVEL_DATABASE_URL=postgresql://{role}:{password}@127.0.0.1:55432/{db}\n',encoding='utf-8')
            targets.append(dict(purpose=kind,database=db,role=role,actualPort=55434,container=name,newDatabase=True))
        docker('exec','-i',name,'psql','-X','-v','ON_ERROR_STOP=1','-U','easytravel_env00_admin','-d','postgres',input=statements)
        with psycopg.connect(host='127.0.0.1',port=55434,user='easytravel_env00_admin',password=secret.read_text(),dbname='postgres') as c:
            cluster_id=c.execute('SELECT system_identifier FROM pg_control_system()').fetchone()[0]
        for target in targets:
            config=load(prefix+'-'+target['purpose']+'.env')
            from psycopg.conninfo import conninfo_to_dict
            settings=conninfo_to_dict(config['EASYTRAVEL_DATABASE_URL'])
            settings['port']='55434'
            with psycopg.connect(**settings) as c:
                identity=c.execute('SELECT current_database(),current_user,inet_server_port()').fetchone()
                assert identity==(target['database'],target['role'],5432)
                n=c.execute("SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')").fetchone()[0]
                assert n==0
                target.update(verifiedEmptyBeforeTests=True,serverPort=5432,clusterSystemIdentifier=str(cluster_id))
        env=os.environ.copy()|load(experiment)|load(prefix+'-retained.env')|dict(PYTHONUTF8='1',PYTHONPATH=str(shim)+os.pathsep+str(ROOT),EASYTRAVEL_ISOLATED_TRANSPORT_PORT='55434')
        subprocess.run([sys.executable,'-m','easytravel.backend.infrastructure.migrate'],env=env,check=True,capture_output=True)
        subprocess.run([sys.executable,'-c',"import asyncio;from easytravel.spike02.database import initialize as a;from easytravel.spike03.database import initialize as b;from easytravel.m000.database import initialize as c\nasync def main():\n await a();await b();await c()\nwith asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as r:r.run(main())"],env=env,check=True,capture_output=True)
        result=subprocess.run([sys.executable,'scripts/verify_m105b_backend.py','--database-prefix',prefix,'--experiment-env',experiment,'--retained-env',prefix+'-retained.env',*(['--browser-only'] if args.browser_only else [])],env=env,check=False)
        status=result.returncode
    finally:
        pth.unlink(missing_ok=True)
        after=fingerprint()
        assert before==after,'Existing experiment tables changed while isolated suite ran'
        Path('Docs/M1-05b_数据库隔离.json').write_text(json.dumps(dict(isolatedContainer=name,newVolume=name+'-data',isolatedPort=55434,nominalGuardPort=55432,testProcessTransportMapping=True,transportHookRemoved=not pth.exists(),targets=targets,existingENV00Uninterrupted=True,retained=True,existingExperimentBefore=before,existingExperimentAfter=after,unchanged=True),ensure_ascii=False,indent=2),encoding='utf-8')
        docker('stop',name)
    raise SystemExit(status)
if __name__=='__main__':main()
