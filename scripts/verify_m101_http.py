"""Exercise a real loopback HTTP server against an explicitly migrated dedicated DB.

Creates one retained example Trip. Tokens stay in memory/environment and are never
printed or passed in a URL/command argument. No migration or database reset occurs.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, build_opener
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    token_a, token_b = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    env = os.environ.copy()
    if "EASYTRAVEL_DATABASE_URL" not in env:
        raise SystemExit("Explicit dedicated migrated database required")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env.update(EASYTRAVEL_APP_ENVIRONMENT="local", EASYTRAVEL_APP_LOCAL_BUSINESS_ENABLED="true",
        EASYTRAVEL_APP_LOCAL_IDENTITIES=json.dumps([{"actor":"http_evidence_a","token":token_a},{"actor":"http_evidence_b","token":token_b}]),
        EASYTRAVEL_APP_LOCAL_ALLOWED_HOSTS=json.dumps([f"127.0.0.1:{port}"]),
        EASYTRAVEL_APP_LOCAL_ALLOWED_ORIGINS=json.dumps(["http://127.0.0.1:5173"]), PYTHONIOENCODING="utf-8")
    base = f"http://127.0.0.1:{port}"
    body = {field:{"state":"unanswered","value":None} for field in (
        "origin","destination","dates","days","travelers","target_budget_cents","hard_limit_cents","transport","hotel","restrictions")}
    body.update(destination={"state":"provided","value":"杭州"}, raw_text="上海出发，国内三天自由行，其他信息稍后补充")
    payload = {"requirements":body}
    key = str(uuid4())
    opener = build_opener(ProxyHandler({}))

    def call(method, path, *, token=None, data=None, extra=None):
        headers = {"Accept":"application/json"}
        if token: headers["Authorization"] = "Bearer " + token
        if data is not None: headers["Content-Type"] = "application/json"
        if extra: headers.update(extra)
        request = Request(base+path, data=json.dumps(data,ensure_ascii=False).encode() if data is not None else None,
                          headers=headers,method=method)
        try:
            response = opener.open(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, dict(response.headers), json.load(response)

    command = [sys.executable,"-m","easytravel.backend.local_server","--port",str(port)]
    server = subprocess.Popen(command, env=env, cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    lines = []
    try:
        for _ in range(100):
            if server.poll() is not None:
                raise RuntimeError("Loopback HTTP process exited before readiness")
            try:
                if call("GET","/api/v1/health")[0] == 200: break
            except (URLError,TimeoutError): pass
            time.sleep(0.1)
        else: raise RuntimeError("Loopback HTTP process not ready")
        lines += ["Real loopback HTTP server; dedicated PostgreSQL; credentials omitted", "POST /api/v1/trips", "Idempotency-Key: "+key,
                  "Request: "+json.dumps(payload,ensure_ascii=False)]
        status, response_headers, first = call("POST","/api/v1/trips",token=token_a,data=payload,extra={"Idempotency-Key":key,"Origin":"http://127.0.0.1:5173"})
        assert status == 201, (status,first)
        assert response_headers.get("cache-control") == "no-store" or response_headers.get("Cache-Control") == "no-store"
        lines += ["HTTP "+str(status)+" Cache-Control: no-store",json.dumps(first,ensure_ascii=False)]
        path = "/api/v1/trips/"+first["trip_id"]
        status, _, read = call("GET",path,token=token_a)
        assert status == 200 and read == first
        lines += ["GET "+path,"HTTP 200 Cache-Control: no-store",json.dumps(read,ensure_ascii=False)]
        status, _, replay = call("POST","/api/v1/trips",token=token_a,data=payload,extra={"Idempotency-Key":key})
        assert status==201 and replay==first
        lines += ["Replay same key/body: HTTP 201, identical persisted TripView"]
        changed={"requirements":body|{"destination":{"state":"provided","value":"苏州"}}}
        for label, method, uri, token, data, extra, expected, code in [
            ("same key different input","POST","/api/v1/trips",token_a,changed,{"Idempotency-Key":key},409,"idempotency_key_reused"),
            ("cross owner","GET",path,token_b,None,None,404,"resource_not_found"),
            ("missing identity","GET",path,None,None,None,401,"authentication_required"),
            ("untrusted origin","GET",path,token_a,None,{"Origin":"https://evil.example"},403,"origin_not_allowed"),
        ]:
            status,_,error=call(method,uri,token=token,data=data,extra=extra)
            assert status==expected and error["error"]["code"]==code
            lines += [label+": HTTP "+str(status)+" "+json.dumps(error)]
        health=call("GET","/api/v1/health")[2]
        assert health["dependencies"]["database"]=="not_checked"
        lines += ["Health: "+json.dumps(health)]
    finally:
        server.terminate()
        try: server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill(); server.wait(timeout=5)
    # A fresh process/server reads persisted state. Token mappings remain stable
    # only for this local verification invocation; they are not stored in the DB.
    restarted=subprocess.Popen(command,env=env,cwd=root,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                status,_,after=call("GET",path,token=token_a)
                if status==200: break
            except (URLError,TimeoutError): pass
            time.sleep(0.1)
        else: raise RuntimeError("Restarted server not ready")
        assert after==first
        lines += ["Independent restarted HTTP process: GET 200, original persisted input and IDs unchanged"]
    finally:
        restarted.terminate()
        try: restarted.wait(timeout=10)
        except subprocess.TimeoutExpired:
            restarted.kill(); restarted.wait(timeout=5)

    from easytravel.backend.infrastructure.database import connection,load_database_settings
    async def database_evidence():
        async with connection(load_database_settings()) as conn:
            version=await (await conn.execute("SHOW server_version")).fetchone()
            migrations=await (await conn.execute("SELECT version,checksum FROM easytravel.schema_migrations ORDER BY version")).fetchall()
            counts={}
            from psycopg import sql
            for table in ("trips","trip_intakes","trip_intake_versions","api_commands","working_drafts"):
                counts[table]=(await (await conn.execute(sql.SQL("SELECT count(*) AS n FROM easytravel.{} WHERE trip_id=%s").format(sql.Identifier(table)),(first["trip_id"],))).fetchone())["n"]
            return version,migrations,counts
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform=="win32" else None) as runner:
        version,migrations,counts=runner.run(database_evidence())
    lines += ["PostgreSQL: "+json.dumps(version),"Migrations: "+json.dumps(migrations),"Rows for evidence Trip: "+json.dumps(counts)]
    for path_sql in sorted((root/"easytravel/backend/infrastructure/migrations").glob("*.sql")):
        lines += [path_sql.name+" SHA256 "+hashlib.sha256(path_sql.read_bytes()).hexdigest()]
    assert all(counts[t]==1 for t in ("trips","trip_intakes","trip_intake_versions","api_commands")) and counts["working_drafts"]==0
    output="\n".join(lines)+"\n"
    assert token_a not in output and token_b not in output and os.environ['EASYTRAVEL_DATABASE_URL'] not in output
    Path(args.log).write_text(output,encoding="utf-8")
    print(output)


if __name__=="__main__":
    main()
