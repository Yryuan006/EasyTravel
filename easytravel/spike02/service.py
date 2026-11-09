"""Explicit single-executor entry point; decisions are durable before resume."""
import asyncio
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from pydantic import TypeAdapter

from ..contracts import ID, TripRequirements
from .database import business_connection, connection_string
from .repository import GRAPH_VERSION, OWNER, Repository, WorkflowError
from .workflow import bound_proposal, build_graph


class WorkflowService:
    def __init__(self, saver, repository, adapter):
        self.saver, self.repository = saver, repository
        self.graph = build_graph(saver, repository, adapter)
        self.lock = asyncio.Lock()

    @staticmethod
    def config(thread_id):
        TypeAdapter(ID).validate_python(thread_id)
        return {"configurable": {"thread_id": thread_id}}

    @staticmethod
    def check_actor(actor):
        if actor != OWNER:
            raise WorkflowError("wrong_actor")

    async def _inspect(self, thread_id):
        snapshot = await self.graph.aget_state(self.config(thread_id))
        if not snapshot.values:
            raise WorkflowError("not_started")
        state = snapshot.values
        proposal = self.repository.validate(await self.repository.proposal(thread_id))
        # A crash after freeze but before its checkpoint may leave generate pending.
        if "proposal_id" in state:
            await bound_proposal(self.repository, state)
        elif snapshot.next != ("generate",) or state.get("graph_version") != GRAPH_VERSION:
            raise WorkflowError("unmatched_checkpoint")
        decision = await self.repository.decision(proposal["proposal_id"])
        receipt = await self.repository.receipt(proposal)
        pending_interrupt = any(task.interrupts for task in snapshot.tasks)
        approval_pending = snapshot.next == ("wait",) and any(
            task.name == "wait" and any(
                isinstance(item.value, dict)
                and item.value.get("proposal_id") == proposal["proposal_id"]
                and item.value.get("candidate_hash") == proposal["request_hash"]
                and item.value.get("working_revision") == state["working_revision"]
                for item in task.interrupts)
            for task in snapshot.tasks)
        error_count = sum(bool(task.error) for task in snapshot.tasks)
        status = "waiting" if pending_interrupt else "recoverable"
        if pending_interrupt and error_count:
            # A failed wait task may retain a consumed, invalid resume value.
            # Don't guess or persist another decision against that checkpoint.
            status = "inconsistent"
        if not snapshot.next:
            if (not decision or state.get("decision_id") != decision["decision_id"]):
                raise WorkflowError("terminal_state_inconsistent")
            if state.get("outcome") == "approved" and decision["choice"] == "approve" and receipt:
                if state.get("result_id") != receipt["result_id"]:
                    raise WorkflowError("result_mismatch")
                status = "approved"
            elif state.get("outcome") == "rejected" and decision["choice"] == "reject" and receipt is None:
                status = "rejected"
            else:
                raise WorkflowError("terminal_state_inconsistent")
        return dict(thread_id=thread_id, proposal_id=proposal["proposal_id"],
                    candidate_hash=proposal["request_hash"], operation_id=proposal["operation_id"],
                    status=status, decision_id=decision["decision_id"] if decision else None,
                    choice=decision["choice"] if decision else None,
                    result_id=receipt["result_id"] if receipt else None,
                    business_committed=receipt is not None, next=list(snapshot.next),
                    task_error_count=error_count, approval_pending=approval_pending and not error_count)

    async def inspect(self, thread_id, *, actor=OWNER):
        self.check_actor(actor)
        async with self.lock:
            return await self._inspect(thread_id)

    async def start(self, thread_id, requirements: TripRequirements, *, working_revision=1, actor=OWNER):
        self.check_actor(actor)
        if type(working_revision) is not int or working_revision < 1:
            raise WorkflowError("invalid_working_revision")
        async with self.lock:
            config = self.config(thread_id)
            snapshot = await self.graph.aget_state(config)
            if snapshot.values or await self.repository.proposal(thread_id):
                raise WorkflowError("already_started")
            await self.graph.ainvoke(dict(thread_id=thread_id,
                requirements=requirements.model_dump(mode="json"), working_revision=working_revision,
                graph_version=GRAPH_VERSION), config, durability="sync")
            return await self._inspect(thread_id)

    async def _continue(self, thread_id):
        for _ in range(3):
            current = await self._inspect(thread_id)
            if current["status"] == "inconsistent":
                raise WorkflowError("checkpoint_requires_review")
            if current["status"] in {"approved", "rejected"}:
                return current
            if current["status"] == "waiting":
                if not current["decision_id"]:
                    return current
                command = Command(resume={"decision_id": current["decision_id"]})
            else:
                command = None  # Decision consumed: continue pending nodes, don't resume again.
            await self.graph.ainvoke(command, self.config(thread_id), durability="sync")
        raise WorkflowError("recovery_did_not_converge")

    async def continue_run(self, thread_id, *, actor=OWNER):
        self.check_actor(actor)
        async with self.lock:
            return await self._continue(thread_id)

    async def decide(self, thread_id, *, proposal_id, candidate_hash, request_id, choice, actor=OWNER):
        self.check_actor(actor)
        TypeAdapter(ID).validate_python(request_id)
        async with self.lock:
            current = await self._inspect(thread_id)
            if current["status"] == "inconsistent":
                raise WorkflowError("checkpoint_requires_review")
            if not current["decision_id"] and not current["approval_pending"]:
                raise WorkflowError("approval_requires_waiting_state")
            proposal = self.repository.validate(await self.repository.proposal(thread_id))
            await self.repository.save_decision(proposal, actor=actor, proposal_id=proposal_id,
                candidate_hash=candidate_hash, request_id=request_id, choice=choice)
            self.repository.fault("decision_saved")
            return await self._continue(thread_id)


@asynccontextmanager
async def open_service(adapter, *, fault=lambda _: None):
    # Caller must initialize explicitly before opening workers.
    async with AsyncPostgresSaver.from_conn_string(connection_string(checkpoint=True)) as saver:
        async with business_connection() as conn:
            yield WorkflowService(saver, Repository(conn, fault), adapter)
