"""Process boundary driver for M0-00 evidence; no HTTP API or real model."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from ..model_adapter import MockAdapter, ModelReply
from ..spike03.rules import ChangeError
from .database import initialize
from .workflow import open_service


def emit(**data):
    print(json.dumps(dict(pid=os.getpid(), **data), ensure_ascii=False), flush=True)


async def run(args):
    if args.action == "init":
        await initialize()
        emit(event="initialized")
        return
    request = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
    output = request.pop("mock_output", None)
    adapter = MockAdapter(*([ModelReply(json.dumps(output))] if output is not None else []))

    def fault(point):
        if point == args.crash_at:
            emit(event="forced_exit", point=point)
            os._exit(75)

    async with open_service(adapter, fault=fault) as service:
        result = await getattr(service, args.action)(**request)
        conn = service.plan.conn
    emit(event="result", result=result, adapter_calls=adapter.calls, connection_closed=conn.closed)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["init", "start", "inspect", "decide", "continue_run"])
    parser.add_argument("--request-file")
    parser.add_argument("--crash-at", choices=["proposal_committed", "decision_committed", "after_change_commit"])
    args = parser.parse_args()
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
            runner.run(asyncio.wait_for(run(args), timeout=30))
        return 0
    except Exception as error:
        emit(event="failed", error_type=type(error).__name__,
             code=str(error) if isinstance(error, ChangeError) else "integration_failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
