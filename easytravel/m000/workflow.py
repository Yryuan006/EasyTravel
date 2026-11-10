"""Mock Agent -> typed proposal -> domain level -> interrupt -> PlanService.

Only the application service persists user decisions and creates authorization.
Graph state and Command(resume=...) are never authorization sources.
"""
import asyncio
from contextlib import asynccontextmanager
from typing import TypedDict
from uuid import uuid4

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from ..spike02.repository import OWNER
from ..spike03.rules import ChangeError
from ..spike03.service import PlanService
from .database import checkpoint_dsn, connection

GRAPH_VERSION = "m000-safe-change-v1"


class State(TypedDict, total=False):
    thread_id: str
    trip_id: str
    operation_id: str
    graph_version: str
    proposal_id: str
    level: str
    choice: str
    outcome: str
    result: dict


class IntegrationService:
    """Single executor per run; no multi-instance concurrency guarantee."""

    def __init__(self, saver, plan, adapter, *, fault=lambda _: None):
        self.plan, self.adapter, self.fault = plan, adapter, fault
        self.lock = asyncio.Lock()
        graph = StateGraph(State)
        graph.add_node("generate", self._generate)
        graph.add_node("wait", self._wait)
        graph.add_node("apply", self._apply)
        graph.add_node("reject", self._reject)
        graph.add_edge(START, "generate")
        graph.add_conditional_edges("generate", lambda s: "wait" if s["level"] == "L3" else "apply")
        graph.add_conditional_edges("wait", lambda s: "apply" if s["choice"] == "approve" else "reject")
        graph.add_edge("apply", END)
        graph.add_edge("reject", END)
        self.graph = graph.compile(checkpointer=saver)

    @staticmethod
    def config(thread_id):
        PlanService.identifier(thread_id)
        return {"configurable": {"thread_id": thread_id}}

    async def _binding(self, state):
        if state.get("graph_version") != GRAPH_VERSION:
            raise ChangeError("graph_version_mismatch")
        row = await (await self.plan.conn.execute(
            "SELECT * FROM integration_runs WHERE thread_id=%s", (state["thread_id"],))).fetchone()
        if row is None:
            return None, None
        proposal = await self.plan.repository.proposal(row["proposal_id"])
        self.plan.validate_proposal(proposal)
        if (state["trip_id"] != row["trip_id"] or state["operation_id"] != row["operation_id"]
                or proposal.trip_id != row["trip_id"]
                or ("proposal_id" in state and state["proposal_id"] != proposal.proposal_id)
                or ("level" in state and state["level"] != proposal.level)):
            raise ChangeError("checkpoint_binding_mismatch")
        return row, proposal

    async def _generate(self, state):
        row, proposal = await self._binding(state)
        if row is None:
            # Freeze proposal and run binding atomically, before graph checkpoint.
            async with self.plan.conn.transaction():
                proposal = await self.plan.propose_change(state["trip_id"], self.adapter)
                await self.plan.conn.execute(
                    "INSERT INTO integration_runs(thread_id,trip_id,proposal_id,operation_id) VALUES (%s,%s,%s,%s)",
                    (state["thread_id"], state["trip_id"], proposal.proposal_id, state["operation_id"]))
            self.fault("proposal_committed")
        return dict(proposal_id=proposal.proposal_id, level=proposal.level)

    @staticmethod
    def disclosure(proposal):
        return dict(proposal_id=proposal.proposal_id, candidate_hash=proposal.candidate_hash,
            base_working_revision=proposal.output.base_working_revision,
            item_ids=list(proposal.touched_item_ids), candidate=proposal.candidate.model_dump(mode="json"),
            explanation=proposal.output.explanation, level=proposal.level)

    async def _wait(self, state):
        row, proposal = await self._binding(state)
        if proposal.level != "L3":
            raise ChangeError("unexpected_approval")
        resumed = interrupt(self.disclosure(proposal))
        # Re-read: a resume payload must match a durable application decision.
        row, _ = await self._binding(state)
        if not row["decision_id"] or resumed != {"decision_id": row["decision_id"]}:
            raise ChangeError("invalid_resume_decision")
        return dict(choice=row["choice"])

    async def _apply(self, state):
        row, proposal = await self._binding(state)
        if proposal.level == "L3" and row["choice"] != "approve":
            raise ChangeError("specific_authorization_required")
        result = await self.plan.apply_working_change(proposal.proposal_id,
            operation_id=row["operation_id"], authorization_id=row["authorization_id"])
        return dict(outcome="applied", result=result)

    async def _reject(self, state):
        row, _ = await self._binding(state)
        if row["choice"] != "reject":
            raise ChangeError("rejection_required")
        return dict(outcome="rejected")

    async def _snapshot(self, thread_id):
        snapshot = await self.graph.aget_state(self.config(thread_id))
        if not snapshot.values:
            raise ChangeError("not_started")
        row, proposal = await self._binding(snapshot.values)
        errors = any(task.error for task in snapshot.tasks)
        pending = bool(proposal and snapshot.next == ("wait",) and not errors and any(
            task.name == "wait" and any(i.value == self.disclosure(proposal) for i in task.interrupts)
            for task in snapshot.tasks))
        return snapshot, row, proposal, pending

    async def inspect(self, thread_id, *, actor=OWNER):
        self.plan.actor(actor)
        async with self.lock:
            snapshot, row, proposal, pending = await self._snapshot(thread_id)
            return dict(thread_id=thread_id, status=snapshot.values.get("outcome", "waiting" if pending else "recoverable"),
                approval_pending=pending, next=list(snapshot.next),
                proposal=self.disclosure(proposal) if proposal else None,
                operation_id=snapshot.values["operation_id"],
                decision_id=row["decision_id"] if row else None, result=snapshot.values.get("result"))

    async def start(self, thread_id, trip_id, *, actor=OWNER):
        self.plan.actor(actor)
        self.plan.identifier(trip_id)
        async with self.lock:
            config = self.config(thread_id)
            if (await self.graph.aget_state(config)).values:
                raise ChangeError("already_started")
            await self.graph.ainvoke(dict(thread_id=thread_id, trip_id=trip_id,
                operation_id=str(uuid4()), graph_version=GRAPH_VERSION), config, durability="sync")
        return await self.inspect(thread_id, actor=actor)

    async def _continue(self, thread_id):
        snapshot, row, _, pending = await self._snapshot(thread_id)
        if not snapshot.next:
            return
        if snapshot.next == ("wait",) and any(task.error for task in snapshot.tasks):
            raise ChangeError("checkpoint_requires_review")
        if pending and not row["decision_id"]:
            return
        command = Command(resume={"decision_id": row["decision_id"]}) if pending else None
        await self.graph.ainvoke(command, self.config(thread_id), durability="sync")

    async def continue_run(self, thread_id, *, actor=OWNER):
        self.plan.actor(actor)
        async with self.lock:
            await self._continue(thread_id)
        return await self.inspect(thread_id, actor=actor)

    async def decide(self, thread_id, *, request_id, choice, proposal_id, candidate_hash,
                     base_working_revision, item_ids, explicit_authorization, actor=OWNER):
        self.plan.actor(actor)
        self.plan.identifier(request_id)
        if choice not in {"approve", "reject"} or type(base_working_revision) is not int:
            raise ChangeError("invalid_decision")
        if choice == "approve" and explicit_authorization is not True:
            raise ChangeError("explicit_authorization_required")
        async with self.lock:
            _, row, proposal, pending = await self._snapshot(thread_id)
            if not row or (not row["decision_id"] and not pending):
                raise ChangeError("approval_requires_waiting_state")
            if (proposal_id != proposal.proposal_id or candidate_hash != proposal.candidate_hash
                    or base_working_revision != proposal.output.base_working_revision
                    or tuple(sorted(item_ids)) != proposal.touched_item_ids):
                raise ChangeError("authorization_scope_mismatch")
            if row["decision_id"]:
                if row["request_id"] != request_id or row["choice"] != choice:
                    raise ChangeError("decision_conflict")
            else:
                # Grant and decision share a transaction: a crash cannot orphan a grant.
                async with self.plan.conn.transaction():
                    authorization = None
                    if choice == "approve":
                        authorization = await self.plan.authorize_change(proposal_id,
                            seen_candidate_hash=candidate_hash, item_ids=item_ids,
                            explicit_authorization=True, actor=actor)
                    await self.plan.conn.execute(
                        "UPDATE integration_runs SET decision_id=%s,request_id=%s,choice=%s,authorization_id=%s WHERE thread_id=%s",
                        (str(uuid4()), request_id, choice,
                         authorization.authorization_id if authorization else None, thread_id))
                self.fault("decision_committed")
            await self._continue(thread_id)
        return await self.inspect(thread_id, actor=actor)


@asynccontextmanager
async def open_service(adapter, *, fault=lambda _: None):
    async with AsyncPostgresSaver.from_conn_string(checkpoint_dsn()) as saver:
        async with connection() as conn:
            yield IntegrationService(saver, PlanService(conn, fault=fault), adapter, fault=fault)
