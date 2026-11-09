"""Short business transactions independent of the checkpoint connection."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from typing import Callable
from uuid import NAMESPACE_URL, uuid5

from psycopg.types.json import Jsonb

from ..contracts import PlanDraftOutput, TripRequirements
from ..rules import evaluate_budget

OWNER = "demo_owner"
GRAPH_VERSION = "spike02-v1"


class WorkflowError(Exception):
    """Stable error codes; do not include DSNs or raw database exceptions."""


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def stable_id(kind: str, thread_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"easytravel/{GRAPH_VERSION}/{kind}/{thread_id}"))


class Repository:
    def __init__(self, connection, fault: Callable[[str], None] = lambda _: None):
        self.conn, self.fault = connection, fault

    async def proposal(self, thread_id: str):
        return await (await self.conn.execute(
            "SELECT * FROM proposals WHERE thread_id = %s", (thread_id,))).fetchone()

    async def freeze(self, thread_id: str, payload: dict):
        envelope = dict(thread_id=thread_id, owner_id=OWNER, graph_version=GRAPH_VERSION,
                        proposal_id=stable_id("proposal", thread_id),
                        operation_id=stable_id("operation", thread_id), payload=payload)
        request_hash = digest(envelope)
        async with self.conn.transaction():
            await self.conn.execute(
                "INSERT INTO proposals(thread_id, proposal_id, operation_id, owner_id, graph_version, request_hash, payload) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (thread_id) DO NOTHING",
                (thread_id, envelope["proposal_id"], envelope["operation_id"], OWNER,
                 GRAPH_VERSION, request_hash, Jsonb(payload)))
            row = await self.proposal(thread_id)
            if row["request_hash"] != request_hash:
                raise WorkflowError("proposal_conflict")
        return row

    def validate(self, row):
        if row is None:
            raise WorkflowError("proposal_not_found")
        envelope = {key: row[key] for key in (
            "thread_id", "owner_id", "graph_version", "proposal_id", "operation_id", "payload")}
        if (row["owner_id"] != OWNER or row["graph_version"] != GRAPH_VERSION
                or digest(envelope) != row["request_hash"]):
            raise WorkflowError("proposal_integrity_error")
        return row

    async def decision(self, proposal_id: str):
        return await (await self.conn.execute(
            "SELECT * FROM decisions WHERE proposal_id = %s", (proposal_id,))).fetchone()

    async def save_decision(self, proposal, *, actor: str, proposal_id: str,
                            request_id: str, choice: str, candidate_hash: str):
        if actor != OWNER:
            raise WorkflowError("wrong_actor")
        if proposal_id != proposal["proposal_id"] or candidate_hash != proposal["request_hash"]:
            raise WorkflowError("wrong_proposal_or_hash")
        if choice not in {"approve", "reject"}:
            raise WorkflowError("invalid_choice")
        request_hash = digest(dict(proposal_id=proposal_id, actor=actor, choice=choice,
                                   candidate_hash=candidate_hash))
        async with self.conn.transaction():
            existing_request = await (await self.conn.execute(
                "SELECT * FROM decision_requests WHERE request_id=%s", (request_id,))).fetchone()
            if existing_request and existing_request["request_hash"] != request_hash:
                raise WorkflowError("request_id_conflict")
            decision = await self.decision(proposal_id)
            if decision and decision["choice"] != choice:
                raise WorkflowError("terminal_decision_conflict")
            if not decision:
                await self.conn.execute(
                    "INSERT INTO decisions(proposal_id, decision_id, owner_id, choice) VALUES (%s,%s,%s,%s)",
                    (proposal_id, stable_id("decision", proposal["thread_id"]), actor, choice))
                decision = await self.decision(proposal_id)
            if not existing_request:
                await self.conn.execute(
                    "INSERT INTO decision_requests(request_id, request_hash, decision_id) VALUES (%s,%s,%s)",
                    (request_id, request_hash, decision["decision_id"]))
        return decision

    async def receipt(self, proposal):
        row = await (await self.conn.execute(
            "SELECT * FROM receipts WHERE operation_id=%s", (proposal["operation_id"],))).fetchone()
        if row and row["request_hash"] != proposal["request_hash"]:
            raise WorkflowError("operation_id_conflict")
        return row

    async def execute_experiment(self, proposal, decision_id: str):
        """Record one accepted candidate, never apply a formal itinerary or confirm it."""
        self.validate(proposal)
        async with self.conn.transaction():
            decision = await self.decision(proposal["proposal_id"])
            if (not decision or decision["choice"] != "approve"
                    or decision["decision_id"] != decision_id):
                raise WorkflowError("approval_required")
            existing = await self.receipt(proposal)
            if existing:
                return existing["result_id"]
            payload = proposal["payload"]
            candidate = PlanDraftOutput.model_validate(payload["candidate"])
            requirements = TripRequirements.model_validate(payload["requirements"])
            budget = evaluate_budget(candidate, requirements, {}, now=datetime.now(timezone.utc))
            if budget.constraint == "OVER_LIMIT":
                raise WorkflowError("known_costs_exceed_hard_limit")
            result_id = stable_id("result", proposal["thread_id"])
            await self.conn.execute(
                "INSERT INTO results(operation_id, result_id, proposal_id, payload) VALUES (%s,%s,%s,%s)",
                (proposal["operation_id"], result_id, proposal["proposal_id"],
                 Jsonb(dict(kind="accepted_candidate", budget=asdict(budget),
                            plan_confirmation="UNCONFIRMED", booked=False, readiness="NOT_ASSESSED"))))
            self.fault("before_business_commit")
            await self.conn.execute(
                "INSERT INTO receipts(operation_id, request_hash, result_id) VALUES (%s,%s,%s)",
                (proposal["operation_id"], proposal["request_hash"], result_id))
        self.fault("after_business_commit")
        return result_id
