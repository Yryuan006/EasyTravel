"""Generate/freeze -> interrupt -> service transaction (or reject)."""
from dataclasses import asdict
from datetime import datetime, timezone
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..application import generate_draft
from ..contracts import TripRequirements
from .repository import GRAPH_VERSION, WorkflowError


class State(TypedDict, total=False):
    thread_id: str
    requirements: dict
    working_revision: int
    graph_version: str
    proposal_id: str
    operation_id: str
    candidate_hash: str
    decision_id: str
    choice: str
    outcome: str
    result_id: str | None


async def bound_proposal(repository, state):
    row = repository.validate(await repository.proposal(state["thread_id"]))
    for key, column in (("proposal_id", "proposal_id"), ("operation_id", "operation_id"),
                        ("candidate_hash", "request_hash")):
        if state.get(key) != row[column]:
            raise WorkflowError("checkpoint_proposal_mismatch")
    if (state.get("graph_version") != GRAPH_VERSION
            or state["requirements"] != row["payload"]["requirements"]
            or state["working_revision"] != row["payload"]["working_revision"]):
        raise WorkflowError("checkpoint_context_mismatch")
    return row


def build_graph(saver, repository, adapter):
    async def generate(state: State):
        proposal = await repository.proposal(state["thread_id"])
        if proposal is None:
            requirements = TripRequirements.model_validate(state["requirements"])
            evaluated = await generate_draft(adapter, requirements, {}, now=datetime.now(timezone.utc))
            if evaluated.budget.constraint == "OVER_LIMIT":
                raise WorkflowError("known_costs_exceed_hard_limit")
            proposal = await repository.freeze(state["thread_id"], dict(
                candidate=evaluated.draft.model_dump(mode="json"),
                requirements=requirements.model_dump(mode="json"),
                working_revision=state["working_revision"], budget=asdict(evaluated.budget)))
        repository.validate(proposal)
        if (state["requirements"] != proposal["payload"]["requirements"]
                or state["working_revision"] != proposal["payload"]["working_revision"]):
            raise WorkflowError("generation_context_mismatch")
        return dict(proposal_id=proposal["proposal_id"], operation_id=proposal["operation_id"],
                    candidate_hash=proposal["request_hash"], graph_version=GRAPH_VERSION)

    async def wait(state: State):
        proposal = await bound_proposal(repository, state)
        resumed = interrupt(dict(proposal_id=proposal["proposal_id"],
                                 candidate_hash=proposal["request_hash"],
                                 working_revision=state["working_revision"],
                                 budget=proposal["payload"]["budget"]))
        decision = await repository.decision(proposal["proposal_id"])
        if (not isinstance(resumed, dict) or set(resumed) != {"decision_id"} or not decision
                or resumed["decision_id"] != decision["decision_id"]):
            raise WorkflowError("invalid_resume_decision")
        return dict(decision_id=decision["decision_id"], choice=decision["choice"])

    async def apply(state: State):
        proposal = await bound_proposal(repository, state)
        result_id = await repository.execute_experiment(proposal, state["decision_id"])
        return dict(outcome="approved", result_id=result_id)

    async def reject(state: State):
        proposal = await bound_proposal(repository, state)
        decision = await repository.decision(proposal["proposal_id"])
        if (not decision or decision["choice"] != "reject"
                or state["decision_id"] != decision["decision_id"]):
            raise WorkflowError("rejection_required")
        return dict(outcome="rejected", result_id=None)

    graph = StateGraph(State)
    graph.add_node("generate", generate)
    graph.add_node("wait", wait)
    graph.add_node("apply", apply)
    graph.add_node("reject", reject)
    graph.add_edge(START, "generate")
    graph.add_edge("generate", "wait")
    graph.add_conditional_edges("wait", lambda state: state["choice"],
                                {"approve": "apply", "reject": "reject"})
    graph.add_edge("apply", END)
    graph.add_edge("reject", END)
    return graph.compile(checkpointer=saver)
