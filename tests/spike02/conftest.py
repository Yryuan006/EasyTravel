import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from easytravel.spike02.database import initialize


def run_async(coro):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        return runner.run(asyncio.wait_for(coro, timeout=35))


@pytest.fixture(scope="session", autouse=True)
def initialized_database():
    # An unavailable database is a failure, never a skipped/passing persistence test.
    try:
        run_async(initialize())
    except Exception as error:
        pytest.fail(f"Dedicated database initialization failed: {type(error).__name__}", pytrace=False)


@pytest.fixture
def input_file(tmp_path, payload, requirements):
    path = tmp_path / "input.json"
    path.write_text(json.dumps(dict(candidate=payload,
                                   requirements=requirements.model_dump(mode="json"))), encoding="utf-8")
    return path


@pytest.fixture
def worker():
    def invoke(action, thread=None, *, expect=0, **kwargs):
        command = [sys.executable, "-m", "easytravel.spike02.worker", action]
        if thread: command += ["--thread", thread]
        for key, value in kwargs.items():
            command += ["--" + key.replace("_", "-"), str(value)]
        result = subprocess.run(command, cwd=Path(__file__).resolve().parents[2],
                                env=os.environ.copy(), text=True, encoding="utf-8",
                                capture_output=True, timeout=40)
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        assert result.returncode == expect, (result.returncode, events)
        assert events, "worker returned no JSON handshake"
        event = events[-1]
        print(json.dumps(dict(action=action, exit_code=result.returncode, **event), ensure_ascii=False))
        return event
    return invoke
