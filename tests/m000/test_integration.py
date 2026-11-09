from copy import deepcopy
import json
from uuid import uuid4

from langgraph.types import Command
import pytest

from easytravel.contracts import PlanDraftOutput
from easytravel.model_adapter import MockAdapter, ModelReply, OutputFailure
from easytravel.m000.workflow import open_service
from easytravel.spike03.rules import ChangeError
from conftest import run_async


def uid():
    return str(uuid4())


def change(payload, level="L3", revision=1):
    index = 0 if level == "L3" else 1
    item, cost = deepcopy(payload["items"][index]), deepcopy(payload["costs"][index])
    item["title"] = "Mock Agent 修改说明"
    if level != "L1":
        item["resource_id"] = cost["query"]["resource_id"] = "replacement-resource"
    return dict(base_working_revision=revision, explanation="合成提案：更新指定项目，费用不变",
        operations=[dict(operation="replace_hotel" if level == "L3" else "replace_activity",
                         target_item_id=item["id"], replacement=item, costs=[cost])])


def mock(output=None):
    return MockAdapter(*([ModelReply(json.dumps(output))] if output is not None else []))


async def seed(service, payload, requirements):
    trip = uid()
    await service.plan.seed_working(trip, PlanDraftOutput.model_validate(payload), requirements,
                                    confirmed_item_ids=("hotel",))
    preview = await service.plan.confirmation_preview(trip)
    result = await service.plan.confirm_plan(trip, operation_id=uid(), expected_revision=1,
        seen_disclosure_hash=preview["disclosure_hash"], explicit_confirmation=True)
    snapshot = await service.plan.get_snapshot(result["latest_confirmed_version_id"])
    return trip, await service.plan.get_working(trip), snapshot


def decision(status, choice="approve", **overrides):
    proposal = status["proposal"]
    return dict(thread_id=status["thread_id"], request_id=uid(), choice=choice,
        proposal_id=proposal["proposal_id"], candidate_hash=proposal["candidate_hash"],
        base_working_revision=proposal["base_working_revision"], item_ids=proposal["item_ids"],
        explicit_authorization=True) | overrides


async def counts(service, thread):
    row = await (await service.plan.conn.execute(
        "SELECT * FROM integration_runs WHERE thread_id=%s", (thread,))).fetchone()
    grants = await (await service.plan.conn.execute(
        "SELECT authorization_id,consumed_by_operation_id FROM specific_authorizations WHERE proposal_id=%s",
        (row["proposal_id"],))).fetchall()
    receipts = await (await service.plan.conn.execute(
        "SELECT result FROM operation_receipts WHERE operation_id=%s", (row["operation_id"],))).fetchall()
    return row, grants, receipts


@pytest.mark.parametrize("level", ["L1", "L2", "L3"])
def test_agent_rules_and_safe_apply(payload, requirements, level):
    async def scenario():
        adapter = mock(change(payload, level))
        async with open_service(adapter) as service:
            trip, before, snapshot = await seed(service, payload, requirements)
            status = await service.start(uid(), trip)
            assert adapter.calls == 1 and status["proposal"]["level"] == level
            if level == "L3":
                assert status["approval_pending"] and status["status"] == "waiting"
                assert await service.plan.get_working(trip) == before
                request = decision(status)
                status = await service.decide(**request)
                assert await service.decide(**request) == status
            assert status["status"] == "applied" and status["result"]["working_revision"] == 2
            assert (await service.plan.get_working(trip)).revision == 2
            assert await service.plan.get_snapshot(snapshot.snapshot_id) == snapshot
            row, grants, receipts = await counts(service, status["thread_id"])
            assert len(receipts) == 1 and len(grants) == (1 if level == "L3" else 0)
            if grants:
                assert grants[0]["consumed_by_operation_id"] == row["operation_id"]
            print(json.dumps(dict(event="integrated_change", level=level, revision_before=before.revision,
                revision_after=2, snapshot_unchanged=True, grants=len(grants), receipts=len(receipts))))
    run_async(scenario())


def test_rejection_preserves_draft_and_snapshot(payload, requirements):
    async def scenario():
        async with open_service(mock(change(payload))) as service:
            trip, before, snapshot = await seed(service, payload, requirements)
            status = await service.start(uid(), trip)
            request = decision(status, "reject")
            result = await service.decide(**request)
            assert result["status"] == "rejected"
            assert await service.decide(**request) == result
            assert await service.plan.get_working(trip) == before
            assert await service.plan.get_snapshot(snapshot.snapshot_id) == snapshot
            _, grants, receipts = await counts(service, status["thread_id"])
            assert not grants and not receipts
            with pytest.raises(ChangeError, match="decision_conflict"):
                await service.decide(**(request | dict(choice="approve")))
            print(json.dumps(dict(event="rejected", draft_unchanged=True, snapshot_unchanged=True, grants=0, receipts=0)))
    run_async(scenario())


@pytest.mark.parametrize("choice", ["approve", "reject"])
def test_first_decision_needs_dynamic_interrupt(payload, requirements, choice):
    async def scenario():
        async with open_service(mock(change(payload))) as service:
            trip, before, _ = await seed(service, payload, requirements)
            service.graph.interrupt_before_nodes = ["wait"]
            status = await service.start(uid(), trip)
            assert not status["approval_pending"]
            with pytest.raises(ChangeError, match="approval_requires_waiting_state"):
                await service.decide(**decision(status, choice))
            row, grants, receipts = await counts(service, status["thread_id"])
            assert row["decision_id"] is None and not grants and not receipts
            assert await service.plan.get_working(trip) == before
            service.graph.interrupt_before_nodes = []
            status = await service.continue_run(status["thread_id"])
            assert status["approval_pending"]
            await service.decide(**decision(status, choice))
    run_async(scenario())


@pytest.mark.parametrize("invalid", ["actor", "hash", "proposal", "revision", "items", "consent"])
def test_specific_user_scope_required(payload, requirements, invalid):
    async def scenario():
        async with open_service(mock(change(payload))) as service:
            trip, before, snapshot = await seed(service, payload, requirements)
            status = await service.start(uid(), trip)
            overrides = {"actor": dict(actor="other"), "hash": dict(candidate_hash="wrong"),
                "proposal": dict(proposal_id=uid()), "revision": dict(base_working_revision=2),
                "items": dict(item_ids=["activity-0"]), "consent": dict(explicit_authorization=False)}
            with pytest.raises(ChangeError):
                await service.decide(**decision(status, **overrides[invalid]))
            row, grants, receipts = await counts(service, status["thread_id"])
            assert row["decision_id"] is None and not grants and not receipts
            assert await service.plan.get_working(trip) == before
            assert await service.plan.get_snapshot(snapshot.snapshot_id) == snapshot
    run_async(scenario())


def test_resume_payload_cannot_create_authorization(payload, requirements):
    async def scenario():
        async with open_service(mock(change(payload))) as service:
            trip, before, _ = await seed(service, payload, requirements)
            status = await service.start(uid(), trip)
            with pytest.raises(ChangeError, match="invalid_resume_decision"):
                await service.graph.ainvoke(Command(resume={"decision_id": uid()}), service.config(status["thread_id"]))
            with pytest.raises(ChangeError, match="checkpoint_requires_review"):
                await service.continue_run(status["thread_id"])
            row, grants, receipts = await counts(service, status["thread_id"])
            assert row["decision_id"] is None and not grants and not receipts
            assert await service.plan.get_working(trip) == before
    run_async(scenario())


@pytest.mark.parametrize("point", ["proposal_committed", "decision_committed", "after_change_commit"])
def test_process_exit_and_graph_recovery(payload, requirements, integration_worker, point):
    async def prepare():
        async with open_service(mock()) as service:
            return await seed(service, payload, requirements)
    trip, before, snapshot = run_async(prepare())
    thread = uid()
    start = dict(thread_id=thread, trip_id=trip, mock_output=change(payload))
    if point == "proposal_committed":
        integration_worker("start", start, crash_at=point, expect=75)
        status = integration_worker("continue_run", dict(thread_id=thread))["result"]
    else:
        status = integration_worker("start", start)["result"]
    assert status["status"] == "waiting"
    request = decision(status)
    if point != "proposal_committed":
        integration_worker("decide", request, crash_at=point, expect=75)
        result = integration_worker("continue_run", dict(thread_id=thread))
    else:
        result = integration_worker("decide", request)
    retry = integration_worker("decide", request)
    recovered = integration_worker("continue_run", dict(thread_id=thread))
    assert result["result"] == retry["result"] == recovered["result"]
    assert len({result["pid"], retry["pid"], recovered["pid"]}) == 3
    assert result["result"]["status"] == "applied"
    assert result["adapter_calls"] == retry["adapter_calls"] == recovered["adapter_calls"] == 0
    async def verify():
        async with open_service(mock()) as service:
            row, grants, receipts = await counts(service, thread)
            assert len(grants) == len(receipts) == 1
            assert grants[0]["consumed_by_operation_id"] == row["operation_id"]
            assert (await service.plan.get_working(trip)).revision == before.revision + 1
            assert await service.plan.get_snapshot(snapshot.snapshot_id) == snapshot
            proposals = await (await service.plan.conn.execute(
                "SELECT proposal_id FROM change_proposals WHERE trip_id=%s", (trip,))).fetchall()
            assert len(proposals) == 1
            print(json.dumps(dict(event="recovery_verified", crash_at=point, revision=2,
                proposals=1, grants=1, consumed_by=row["operation_id"], receipts=1, snapshot_unchanged=True)))
    run_async(verify())


def test_invalid_agent_output_does_not_write_business_data(payload, requirements):
    async def scenario():
        async with open_service(MockAdapter(ModelReply("{}"), ModelReply("{}"))) as service:
            trip, before, snapshot = await seed(service, payload, requirements)
            with pytest.raises(OutputFailure):
                await service.start(uid(), trip)
            assert await service.plan.get_working(trip) == before
            assert await service.plan.get_snapshot(snapshot.snapshot_id) == snapshot
            rows = await (await service.plan.conn.execute(
                "SELECT proposal_id FROM change_proposals WHERE trip_id=%s", (trip,))).fetchall()
            assert not rows
    run_async(scenario())


@pytest.mark.parametrize("case", ["budget", "stale"])
def test_authorization_cannot_bypass_domain_checks(payload, requirements, case):
    async def scenario():
        output = change(payload)
        if case == "budget":
            output["operations"][0]["costs"][0]["amount_cents"] += 1
        async with open_service(mock(output)) as service:
            trip, before, snapshot = await seed(service, payload, requirements)
            status = await service.start(uid(), trip)
            if case == "stale":
                proposal = await service.plan.propose_change(trip, mock(change(payload, "L1")))
                await service.plan.apply_working_change(proposal.proposal_id, operation_id=uid())
                before = await service.plan.get_working(trip)
            with pytest.raises(ChangeError, match="known_costs_exceed_hard_limit" if case == "budget" else "stale_proposal"):
                await service.decide(**decision(status))
            _, grants, receipts = await counts(service, status["thread_id"])
            assert not receipts and all(g["consumed_by_operation_id"] is None for g in grants)
            assert await service.plan.get_working(trip) == before
            assert await service.plan.get_snapshot(snapshot.snapshot_id) == snapshot
    run_async(scenario())


def test_grant_and_decision_rollback_together(payload, requirements):
    async def scenario():
        async with open_service(mock(change(payload))) as service:
            trip, before, _ = await seed(service, payload, requirements)
            status = await service.start(uid(), trip)
            authorize = service.plan.authorize_change

            async def fail_after_grant(*args, **kwargs):
                await authorize(*args, **kwargs)
                raise RuntimeError("decision_transaction_failure")

            service.plan.authorize_change = fail_after_grant
            request = decision(status)
            with pytest.raises(RuntimeError, match="decision_transaction_failure"):
                await service.decide(**request)
            row, grants, receipts = await counts(service, status["thread_id"])
            assert row["decision_id"] is None and not grants and not receipts
            assert await service.plan.get_working(trip) == before
            service.plan.authorize_change = authorize
            assert (await service.decide(**request))["status"] == "applied"
    run_async(scenario())


def test_old_spike_checkpoint_keeps_its_resume_semantics(payload, requirements):
    from easytravel.spike02.database import initialize as initialize_spike02
    from easytravel.spike02.service import open_service as open_spike02

    async def scenario():
        await initialize_spike02()
        thread = uid()
        async with open_spike02(mock(payload)) as old:
            old_status = await old.start(thread, requirements)
            checkpoint = await old.saver.aget_tuple(old.config(thread))
            assert old_status["approval_pending"]
            async with open_service(mock(change(payload))) as service:
                trip, _, _ = await seed(service, payload, requirements)
                status = await service.start(thread, trip)  # Same ID, independent schema.
                assert (await service.decide(**decision(status)))["status"] == "applied"
            assert await old.saver.aget_tuple(old.config(thread)) == checkpoint
            assert await old.inspect(thread) == old_status
            result = await old.decide(thread, proposal_id=old_status["proposal_id"],
                candidate_hash=old_status["candidate_hash"], request_id=uid(), choice="reject")
            assert result["status"] == "rejected"
            print(json.dumps(dict(event="old_checkpoint_isolated", same_thread_id=thread,
                                  checkpoint_unchanged=True, old_resume="rejected")))
    run_async(scenario())


def test_rejected_decision_survives_process_exit(payload, requirements, integration_worker):
    async def prepare():
        async with open_service(mock()) as service:
            return await seed(service, payload, requirements)
    trip, before, snapshot = run_async(prepare())
    thread = uid()
    status = integration_worker("start", dict(thread_id=thread, trip_id=trip, mock_output=change(payload)))["result"]
    request = decision(status, "reject")
    integration_worker("decide", request, crash_at="decision_committed", expect=75)
    result = integration_worker("continue_run", dict(thread_id=thread))["result"]
    assert result["status"] == "rejected"
    assert integration_worker("decide", request)["result"] == result
    async def verify():
        async with open_service(mock()) as service:
            _, grants, receipts = await counts(service, thread)
            assert not grants and not receipts
            assert await service.plan.get_working(trip) == before
            assert await service.plan.get_snapshot(snapshot.snapshot_id) == snapshot
    run_async(verify())
