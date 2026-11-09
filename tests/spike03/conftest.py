import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from easytravel.spike03.database import initialize


def run_async(coro):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        return runner.run(asyncio.wait_for(coro, timeout=35))


@pytest.fixture(scope="session", autouse=True)
def initialized_spike03_database():
    try:
        run_async(initialize())
    except Exception as error:
        pytest.fail(f"Dedicated Spike-03 database initialization failed: {type(error).__name__}", pytrace=False)


@pytest.fixture
def business_worker(tmp_path):
    def invoke(action, request, *, crash_at=None, expect=0):
        path = tmp_path / "request.json"
        path.write_text(json.dumps(request), encoding="utf-8")
        command = [sys.executable, "-m", "easytravel.spike03.worker", action, "--request-file", str(path)]
        if crash_at: command += ["--crash-at", crash_at]
        completed = subprocess.run(command, cwd=Path(__file__).resolve().parents[2], env=os.environ.copy(),
            capture_output=True, text=True, encoding="utf-8", timeout=40)
        events = [json.loads(line) for line in completed.stdout.splitlines() if line.startswith("{")]
        assert completed.returncode == expect, (completed.returncode, events)
        assert events
        event = events[-1]
        print(json.dumps(dict(action=action, exit_code=completed.returncode, **event)))
        return event
    return invoke
