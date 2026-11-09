import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from easytravel.m000.database import initialize


def run_async(coro):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        return runner.run(asyncio.wait_for(coro, timeout=35))


@pytest.fixture(scope="session", autouse=True)
def initialized_m000():
    run_async(initialize())


@pytest.fixture
def integration_worker(tmp_path):
    def invoke(action, request, *, crash_at=None, expect=0):
        path = tmp_path / "integration-request.json"
        path.write_text(json.dumps(request), encoding="utf-8")
        command = [sys.executable, "-m", "easytravel.m000.worker", action, "--request-file", str(path)]
        if crash_at:
            command += ["--crash-at", crash_at]
        completed = subprocess.run(command, cwd=Path(__file__).resolve().parents[2],
            env=os.environ.copy(), capture_output=True, text=True, encoding="utf-8", timeout=40)
        events = [json.loads(line) for line in completed.stdout.splitlines() if line.startswith("{")]
        assert completed.returncode == expect, (completed.returncode, events, completed.stderr)
        assert events
        event = events[-1]
        print(json.dumps(dict(action=action, exit_code=completed.returncode, **event), ensure_ascii=False))
        return event
    return invoke
