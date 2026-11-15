"""Run the accepted 454 tests on a newly created, retained PostgreSQL cluster.

All legacy nominal 55432 connections are redirected to 55434 in test processes.
The transport shim fails closed for any database outside the new allow-list.
Existing ENV-00 is only fingerprinted read-only; old evidence is restored bytewise.
Run from repository root with .venv/Scripts/python.exe.
"""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict

from verify_m108_isolated import docker, fingerprint

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "Docs/M2-01a"

SHIM = '''import os
if os.environ.get('EASYTRAVEL_ISOLATED_TRANSPORT_PORT'):
 import psycopg
 from psycopg.conninfo import conninfo_to_dict
 original_sync=psycopg.Connection.connect
 original_async=psycopg.AsyncConnection.connect
 def mapped(dsn,kwargs):
  c=conninfo_to_dict(dsn,**{k:v for k,v in kwargs.items() if k in ('host','port','dbname','user','password')})
  port=c.get('port'); host=c.get('host'); db=c.get('dbname')
  if port=='1': return dsn,kwargs  # Intentional offline failure tests.
  allowed=set(os.environ['EASYTRAVEL_M201A_ALLOWED_DATABASES'].split(','))
  if host not in ('127.0.0.1','localhost','::1') or port!='55432' or db not in allowed:
   raise RuntimeError('M2-01a test database boundary rejected connection')
  return dsn,kwargs|dict(port=os.environ['EASYTRAVEL_ISOLATED_TRANSPORT_PORT'])
 def connect(cls,conninfo='',**kwargs):
  dsn,kwargs=mapped(conninfo,kwargs); return original_sync(dsn,**kwargs)
 async def async_connect(cls,conninfo='',**kwargs):
  dsn,kwargs=mapped(conninfo,kwargs); return await original_async(dsn,**kwargs)
 psycopg.Connection.connect=classmethod(connect)
 psycopg.AsyncConnection.connect=classmethod(async_connect)
 psycopg.connect=psycopg.Connection.connect
'''


def main():
    OUT.mkdir(exist_ok=True)
    before = fingerprint()  # No shim in this process, read-only old-cluster query.
    info = json.loads(docker("inspect", "easytravel-env00-postgres"))[0]
    assert info["Config"]["Labels"]["com.easytravel.scope"] == "env00"
    assert info["State"]["Running"]
    old_id = docker("exec", "easytravel-env00-postgres", "psql", "-X", "-At", "-U",
                    "easytravel_env00_admin", "-d", "postgres", "-c", "SELECT system_identifier FROM pg_control_system()")
    suffix = uuid4().hex[:10]
    name = "easytravel-m201a-isolated-" + suffix
    secret = ROOT / ".local" / ("m201a-admin-" + suffix)
    secret.write_text(secrets.token_hex(32), encoding="utf-8")
    hook = Path(sys.prefix) / "Lib/site-packages/easytravel_m201a_transport.pth"
    assert not hook.exists(), "Do not replace another run's transport hook"
    shim = ROOT / ".local" / ("m201a-transport-" + suffix)
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(SHIM, encoding="utf-8")
    old_evidence = {p: p.read_bytes() for p in (ROOT / "Docs").glob("M1-*") if p.is_file()}
    targets, configs = [], {}
    started = False
    status = 1
    report = dict(container=name, newVolume=name + "-data", actualPort=55434,
                  nominalGuardPort=55432, targets=targets, oldClusterSystemIdentifier=old_id)
    try:
        docker("run", "-d", "--name", name, "--label", "com.easytravel.scope=m201a-isolated",
               "-p", "127.0.0.1:55434:5432", "-e", "POSTGRES_USER=easytravel_env00_admin",
               "-e", "POSTGRES_PASSWORD_FILE=/run/secrets/admin", "--mount",
               f"type=bind,source={secret},target=/run/secrets/admin,readonly", "--mount",
               f"type=volume,source={name}-data,target=/var/lib/postgresql/data", info["Image"])
        started = True
        isolated = json.loads(docker("inspect", name))[0]
        assert isolated["Config"]["Labels"]["com.easytravel.scope"] == "m201a-isolated"
        assert isolated["NetworkSettings"]["Ports"]["5432/tcp"] == [dict(HostIp="127.0.0.1", HostPort="55434")]
        assert any(m.get("Name") == name + "-data" for m in isolated["Mounts"])
        assert not ({m.get("Name") for m in isolated["Mounts"] if m.get("Name")} &
                    {m.get("Name") for m in info["Mounts"] if m.get("Name")})
        admin = dict(host="127.0.0.1", port=55434, user="easytravel_env00_admin", password=secret.read_text(), dbname="postgres", connect_timeout=1)
        for _ in range(100):
            try:
                with psycopg.connect(**admin) as conn:
                    cluster_id = str(conn.execute("SELECT system_identifier FROM pg_control_system()").fetchone()[0])
                break
            except psycopg.OperationalError:
                time.sleep(.2)
        else:
            raise RuntimeError("New PostgreSQL cluster did not start")
        assert cluster_id != old_id and isolated["Id"] != info["Id"]
        report.update(clusterSystemIdentifier=cluster_id, verifiedDistinctCluster=True, existingENV00Uninterrupted=True)
        statements = []
        passwords = [admin["password"]]
        for kind in ("experiment", "regression", "api", "compat", "gen", "confirm", "retained"):
            role = "easytravel_env00" if kind == "experiment" else "easytravel_m002_" + uuid4().hex[:12]
            db = "easytravel_env00_test" if kind == "experiment" else "easytravel_m002_test_" + uuid4().hex[:12]
            password = secrets.token_hex(32)
            passwords.append(password)
            statements += [f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE;",
                           f"CREATE DATABASE {db} OWNER {role};", f"REVOKE CONNECT ON DATABASE {db} FROM PUBLIC;"]
            dsn = f"postgresql://{role}:{password}@127.0.0.1:55432/{db}"
            key = "EASYTRAVEL_TEST_DATABASE_URL" if kind == "experiment" else "EASYTRAVEL_DATABASE_URL"
            configs[kind] = dsn
            (ROOT / ".local" / f"m201a-{suffix}-{kind}.env").write_text(f"{key}={dsn}\n", encoding="utf-8")
            targets.append(dict(purpose=kind, database=db, role=role, actualPort=55434, newDatabase=True))
        docker("exec", "-i", name, "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U",
               "easytravel_env00_admin", "-d", "postgres", input="\n".join(statements))
        for target in targets:
            settings = conninfo_to_dict(configs[target["purpose"]]) | dict(port="55434")
            with psycopg.connect(**settings) as conn:
                identity = conn.execute("SELECT current_database(),current_user,inet_server_port()").fetchone()
                assert identity == (target["database"], target["role"], 5432)
                assert conn.execute("SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user").fetchone() == (False, False, False)
                assert conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')").fetchone()[0] == 0
            target.update(verifiedEmptyBeforeTests=True, verifiedNonSuperuser=True)
        env = os.environ.copy() | dict(
            EASYTRAVEL_TEST_DATABASE_URL=configs["experiment"], EASYTRAVEL_DATABASE_URL=configs["regression"],
            EASYTRAVEL_M101_TEST_DATABASE_URL=configs["api"], EASYTRAVEL_M103_TEST_DATABASE_URL=configs["compat"],
            EASYTRAVEL_M104_TEST_DATABASE_URL=configs["gen"], EASYTRAVEL_M105_TEST_DATABASE_URL=configs["confirm"],
            EASYTRAVEL_M106_TEST_DATABASE_URL=configs["confirm"], EASYTRAVEL_M107_TEST_DATABASE_URL=configs["confirm"],
            EASYTRAVEL_M101_RETAINED_DATABASE_URL=configs["retained"], EASYTRAVEL_MODEL_MODE="mock",
            LANGGRAPH_STRICT_MSGPACK="true", PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
            PYTHONPATH=str(shim) + os.pathsep + str(ROOT), EASYTRAVEL_ISOLATED_TRANSPORT_PORT="55434",
            EASYTRAVEL_M201A_ALLOWED_DATABASES=",".join(t["database"] for t in targets))
        hook.write_text(str(shim) + "\n", encoding="utf-8")
        with (OUT / "隔离准备.txt").open("w", encoding="utf-8") as stream:
            subprocess.run([sys.executable, "-m", "easytravel.backend.infrastructure.migrate"],
                env=env | dict(EASYTRAVEL_DATABASE_URL=configs["retained"]), stdout=stream, stderr=subprocess.STDOUT, check=True)
            initialize = "import asyncio;from easytravel.spike02.database import initialize as a;from easytravel.spike03.database import initialize as b;from easytravel.m000.database import initialize as c\nasync def main():\n await a();await b();await c()\nwith asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as r:r.run(main())"
            subprocess.run([sys.executable, "-c", initialize], env=env, stdout=stream, stderr=subprocess.STDOUT, check=True)
        with (OUT / "后端454项回归.txt").open("w", encoding="utf-8") as stream:
            result = subprocess.run([sys.executable, "-m", "pytest", "-v", "-s", "--ignore=tests/places"],
                                    env=env, stdout=stream, stderr=subprocess.STDOUT)
        status = result.returncode
        log = (OUT / "后端454项回归.txt").read_text(encoding="utf-8")
        assert all(p not in log for p in passwords), "Credential found in regression log"
        assert "454 passed" in log and status == 0, "Accepted 454-test suite did not pass"
        print("\n".join(log.splitlines()[-8:]))
    finally:
        hook.unlink(missing_ok=True)
        archived = []
        for path, content in old_evidence.items():
            if path.read_bytes() != content:
                dest = OUT / "旧测试本轮证据" / path.name
                dest.parent.mkdir(exist_ok=True)
                dest.write_bytes(path.read_bytes())
                path.write_bytes(content)
                archived.append(path.name)
        after = fingerprint()
        report.update(existingExperimentBefore=before, existingExperimentAfter=after,
                      unchanged=before == after, transportHookRemoved=not hook.exists(),
                      oldEvidenceRestored=all(p.read_bytes() == b for p, b in old_evidence.items()),
                      archivedRegressionEvidence=archived, regressionExit=status, retained=True)
        if started:
            docker("stop", name)
        report["containerStopped"] = started
        (OUT / "数据库隔离.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        assert before == after, "Existing experiment data changed"
    raise SystemExit(status)


if __name__ == "__main__":
    main()
