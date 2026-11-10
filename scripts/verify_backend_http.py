"""Run a temporary local Uvicorn server and save actual HTTP/OpenAPI evidence."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from fastapi.openapi.models import OpenAPI


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    command = [sys.executable, "-m", "uvicorn", "easytravel.backend.main:create_app",
               "--factory", "--host", "127.0.0.1", "--port", str(port)]
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("EASYTRAVEL_APP_")}
    environment["EASYTRAVEL_APP_ENVIRONMENT"] = "local"
    opener = build_opener(ProxyHandler({}))
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as server_log:
        process = subprocess.Popen(command, cwd=root, env=environment,
                                   stdout=server_log, stderr=server_log)
        try:
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Uvicorn exited before HTTP verification")
                try:
                    response = opener.open(f"http://127.0.0.1:{port}/api/v1/health", timeout=1)
                    break
                except URLError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Uvicorn did not become reachable") from None
                    time.sleep(0.1)
            with response:
                print("GET /api/v1/health")
                print(f"HTTP {response.status}")
                print("Content-Type: " + response.headers["Content-Type"])
                print(response.read().decode())
            with opener.open(f"http://127.0.0.1:{port}/openapi.json", timeout=2) as response:
                document = json.load(response)
                OpenAPI.model_validate(document)
                print(f"GET /openapi.json -> HTTP {response.status}; OpenAPI {document['openapi']}")
                print("Paths: " + ", ".join(document["paths"]))
                target = root / "Docs/M0-01_openapi.json"
                target.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                print("Validated and saved Docs/M0-01_openapi.json")
            with opener.open(f"http://127.0.0.1:{port}/docs", timeout=2) as response:
                print(f"GET /docs -> HTTP {response.status}")
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            server_log.seek(0)
            print("Uvicorn output:\n" + server_log.read())
            print("Temporary verification server stopped.")


if __name__ == "__main__":
    main()
