"""Bounded subprocess experiment driver. Credentials only come from environment."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from ..contracts import TripRequirements
from ..model_adapter import MockAdapter, ModelReply
from .database import initialize
from .service import open_service


def emit(**value):
    print(json.dumps(dict(pid=os.getpid(), **value), ensure_ascii=False), flush=True)


async def run(args):
    if args.action == "init":
        await initialize()
        emit(event="initialized")
        return
    adapter = MockAdapter()  # Any accidental generation on resume fails, never silently replays a fixture.
    requirements = None
    if args.action == "start":
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
        adapter = MockAdapter(ModelReply(json.dumps(data["candidate"])))
        requirements = TripRequirements.model_validate(data["requirements"])

    def fault(point):
        if point == args.crash_at:
            emit(event="forced_exit", point=point)
            os._exit(75)

    async with open_service(adapter, fault=fault) as service:
        if args.action == "start":
            result = await service.start(args.thread, requirements)
        elif args.action == "inspect":
            result = await service.inspect(args.thread)
        elif args.action == "continue":
            result = await service.continue_run(args.thread)
        else:
            result = await service.decide(args.thread, proposal_id=args.proposal,
                candidate_hash=args.candidate_hash, request_id=args.request, choice=args.choice,
                actor=args.actor)
        checkpoint_conn, business_conn = service.saver.conn, service.repository.conn
    if not checkpoint_conn.closed or not business_conn.closed:
        raise RuntimeError("connections_not_closed")
    emit(event="result", **result, model_calls=adapter.calls, connections_closed=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["init", "start", "inspect", "decide", "continue"])
    parser.add_argument("--thread")
    parser.add_argument("--input")
    parser.add_argument("--proposal")
    parser.add_argument("--candidate-hash")
    parser.add_argument("--request")
    parser.add_argument("--choice", choices=["approve", "reject"])
    parser.add_argument("--actor", default="demo_owner")
    parser.add_argument("--crash-at", choices=["decision_saved", "before_business_commit", "after_business_commit"])
    args = parser.parse_args()
    os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
            runner.run(asyncio.wait_for(run(args), timeout=30))
        return 0
    except Exception as error:
        emit(event="failed", error_type=type(error).__name__,
             code=str(error) if type(error).__name__ == "WorkflowError" else "operation_failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
