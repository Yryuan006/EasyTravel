"""Independent-process retry driver for committed business operations."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from .database import initialize
from .rules import ChangeError
from .service import open_service


def emit(**data):
    print(json.dumps(dict(pid=os.getpid(), **data), ensure_ascii=False), flush=True)


async def run(args):
    if args.action == "init":
        await initialize()
        emit(event="initialized")
        return
    request = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
    def fault(point):
        if point == args.crash_at:
            emit(event="forced_exit", point=point, operation_id=request["operation_id"])
            os._exit(75)
    async with open_service(fault=fault) as service:
        result = await getattr(service, args.action)(**request)
        conn = service.conn
    assert conn.closed
    emit(event="result", result=result, connection_closed=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["init", "apply_working_change", "confirm_plan"])
    parser.add_argument("--request-file")
    parser.add_argument("--crash-at", choices=["after_change_commit", "after_confirmation_commit"])
    args = parser.parse_args()
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
            runner.run(asyncio.wait_for(run(args), timeout=30))
        return 0
    except Exception as error:
        emit(event="failed", error_type=type(error).__name__,
             code=str(error) if isinstance(error, ChangeError) else "operation_failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
