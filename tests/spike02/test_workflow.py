import json
from uuid import uuid4

import pytest
from langgraph.types import Command

from easytravel.model_adapter import MockAdapter, ModelReply
from easytravel.spike02.database import business_connection, connection_string
from easytravel.spike02.repository import WorkflowError
from easytravel.spike02.service import open_service
from conftest import run_async


def thread_id():
    return "s02-" + uuid4().hex


def decision_args(pending, *, choice="approve", request=None):
    return dict(proposal_id=pending["proposal_id"], candidate_hash=pending["candidate_hash"],
                request_id=request or "request-" + uuid4().hex, choice=choice)


async def counts(thread):
    async with business_connection() as conn:
        row = await (await conn.execute(
            "SELECT (SELECT count(*) FROM results r WHERE r.proposal_id=p.proposal_id) AS results, "
            "(SELECT count(*) FROM receipts r WHERE r.operation_id=p.operation_id) AS receipts, "
            "(SELECT count(*) FROM decisions d WHERE d.proposal_id=p.proposal_id) AS decisions "
            "FROM proposals p WHERE thread_id=%s", (thread,))).fetchone()
        return row


@pytest.mark.parametrize("choice", ["approve", "reject"])
def test_single_process_and_ten_repeats(payload, requirements, choice):
    async def scenario():
        thread = thread_id()
        adapter = MockAdapter(ModelReply(json.dumps(payload)))
        async with open_service(adapter) as service:
            pending = await service.start(thread, requirements)
            assert pending["status"] == "waiting" and pending["next"] == ["wait"]
            assert await counts(thread) == dict(results=0, receipts=0, decisions=0)
            assert (await service.continue_run(thread))["status"] == "waiting"
            request = decision_args(pending, choice=choice)
            result = await service.decide(thread, **request)
            for _ in range(10):
                assert await service.decide(thread, **request) == result
            assert result["status"] == ("approved" if choice == "approve" else "rejected")
            assert await counts(thread) == dict(results=int(choice == "approve"), receipts=int(choice == "approve"), decisions=1)
            assert adapter.calls == 1
            with pytest.raises(WorkflowError, match="already_started"):
                await service.start(thread, requirements)
            checkpoint_conn, business_conn = service.saver.conn, service.repository.conn
        assert checkpoint_conn.closed and business_conn.closed
    run_async(scenario())


@pytest.mark.parametrize("bad", ["actor", "proposal", "hash"])
def test_wrong_identity_or_candidate(payload, requirements, bad):
    async def scenario():
        thread = thread_id()
        async with open_service(MockAdapter(ModelReply(json.dumps(payload)))) as service:
            pending = await service.start(thread, requirements)
            request = decision_args(pending)
            if bad == "actor": request["actor"] = "other_owner"
            if bad == "proposal": request["proposal_id"] = str(uuid4())
            if bad == "hash": request["candidate_hash"] = "wrong"
            with pytest.raises(WorkflowError): await service.decide(thread, **request)
            if bad == "actor":
                with pytest.raises(WorkflowError, match="wrong_actor"):
                    await service.continue_run(thread, actor="other_owner")
            assert await counts(thread) == dict(results=0, receipts=0, decisions=0)
            assert (await service.inspect(thread))["status"] == "waiting"
    run_async(scenario())


@pytest.mark.parametrize("first", ["approve", "reject"])
def test_conflicting_decision(payload, requirements, first):
    async def scenario():
        thread = thread_id()
        async with open_service(MockAdapter(ModelReply(json.dumps(payload)))) as service:
            pending = await service.start(thread, requirements)
            request = decision_args(pending, choice=first)
            result = await service.decide(thread, **request)
            opposite = "reject" if first == "approve" else "approve"
            with pytest.raises(WorkflowError, match="request_id_conflict"):
                await service.decide(thread, **(request | dict(choice=opposite)))
            with pytest.raises(WorkflowError, match="terminal_decision_conflict"):
                await service.decide(thread, **decision_args(pending, choice=opposite))
            alias = decision_args(pending, choice=first)
            assert await service.decide(thread, **alias) == result
            assert await counts(thread) == dict(results=int(first == "approve"), receipts=int(first == "approve"), decisions=1)
    run_async(scenario())


def test_request_key_cannot_bind_another_proposal(payload, requirements):
    async def scenario():
        async with open_service(MockAdapter(ModelReply(json.dumps(payload)), ModelReply(json.dumps(payload)))) as service:
            one, two = thread_id(), thread_id()
            p1, p2 = await service.start(one, requirements), await service.start(two, requirements)
            request = "key-" + uuid4().hex
            await service.decide(one, **decision_args(p1, request=request))
            with pytest.raises(WorkflowError, match="request_id_conflict"):
                await service.decide(two, **decision_args(p2, request=request))
            assert await counts(two) == dict(results=0, receipts=0, decisions=0)
    run_async(scenario())


@pytest.mark.parametrize("action", ["inspect", "continue_run"])
def test_missing_checkpoint_never_regenerates(action):
    async def scenario():
        adapter = MockAdapter()
        async with open_service(adapter) as service:
            with pytest.raises(WorkflowError, match="not_started"):
                await getattr(service, action)(thread_id())
            assert adapter.calls == 0
    run_async(scenario())


def test_resume_must_reference_saved_decision(payload, requirements):
    async def scenario():
        thread = thread_id()
        async with open_service(MockAdapter(ModelReply(json.dumps(payload)))) as service:
            await service.start(thread, requirements)
            with pytest.raises(WorkflowError, match="invalid_resume_decision"):
                await service.graph.ainvoke(Command(resume={"decision_id": "fabricated"}),
                                            service.config(thread), durability="sync")
            current = await service.inspect(thread)
            assert current["status"] == "inconsistent" and not current["business_committed"]
            assert current["task_error_count"] == 1
            with pytest.raises(WorkflowError, match="checkpoint_requires_review"):
                await service.decide(thread, **decision_args(current))
            with pytest.raises(WorkflowError, match="checkpoint_requires_review"):
                await service.continue_run(thread)
            assert await counts(thread) == dict(results=0, receipts=0, decisions=0)
    run_async(scenario())


def test_transaction_failure_rolls_back_and_can_continue(payload, requirements):
    async def scenario():
        thread = thread_id()
        def fail(point):
            if point == "before_business_commit": raise RuntimeError("injected_transaction_failure")
        async with open_service(MockAdapter(ModelReply(json.dumps(payload))), fault=fail) as service:
            pending = await service.start(thread, requirements)
            with pytest.raises(RuntimeError, match="injected_transaction_failure"):
                await service.decide(thread, **decision_args(pending))
            current = await service.inspect(thread)
            assert current["status"] == "recoverable" and current["next"] == ["apply"]
            assert not current["business_committed"]
            assert await counts(thread) == dict(results=0, receipts=0, decisions=1)
        async with open_service(MockAdapter()) as service:
            assert (await service.continue_run(thread))["status"] == "approved"
            assert await counts(thread) == dict(results=1, receipts=1, decisions=1)
    run_async(scenario())


def test_lost_business_connection_never_reports_success(payload, requirements):
    async def scenario():
        thread = thread_id()
        async with open_service(MockAdapter(ModelReply(json.dumps(payload)))) as service:
            pending = await service.start(thread, requirements)
            await service.repository.conn.close()
            with pytest.raises(Exception): await service.decide(thread, **decision_args(pending))
        async with open_service(MockAdapter()) as service:
            assert (await service.inspect(thread))["status"] == "waiting"
            assert await counts(thread) == dict(results=0, receipts=0, decisions=0)
    run_async(scenario())


def test_checkpoint_connection_failure_never_records_decision(payload, requirements):
    async def scenario():
        thread = thread_id()
        async with open_service(MockAdapter(ModelReply(json.dumps(payload)))) as service:
            pending = await service.start(thread, requirements)
            await service.saver.conn.close()
            with pytest.raises(Exception): await service.decide(thread, **decision_args(pending))
        async with open_service(MockAdapter()) as service:
            assert (await service.inspect(thread))["status"] == "waiting"
            assert await counts(thread) == dict(results=0, receipts=0, decisions=0)
    run_async(scenario())


def test_over_limit_candidate_cannot_reach_wait(payload, requirements):
    async def scenario():
        payload["costs"][0]["amount_cents"] += 1
        thread = thread_id()
        async with open_service(MockAdapter(ModelReply(json.dumps(payload)))) as service:
            with pytest.raises(WorkflowError, match="known_costs_exceed_hard_limit"):
                await service.start(thread, requirements)
            assert await service.repository.proposal(thread) is None
    run_async(scenario())


def test_database_guard_rejects_other_targets(monkeypatch):
    monkeypatch.setenv("EASYTRAVEL_TEST_DATABASE_URL", "postgresql://other@127.0.0.1:55432/shared")
    with pytest.raises(ValueError, match="dedicated ENV-00"):
        connection_string()


@pytest.mark.parametrize("choice", ["approve", "reject"])
def test_cross_process_start_exit_and_resume(worker, input_file, choice):
    thread = thread_id()
    start = worker("start", thread, input=input_file)
    assert start["status"] == "waiting" and start["model_calls"] == 1
    # subprocess.run has waited for A to exit; B cannot inherit A's connection or graph.
    query = worker("inspect", thread)
    assert query["status"] == "waiting" and query["model_calls"] == 0
    request = "request-" + uuid4().hex
    args = dict(proposal=start["proposal_id"], candidate_hash=start["candidate_hash"], request=request, choice=choice)
    resumed = worker("decide", thread, **args)
    assert resumed["status"] == ("approved" if choice == "approve" else "rejected")
    assert resumed["model_calls"] == 0 and resumed["pid"] != start["pid"]
    repeated = worker("decide", thread, **args)
    assert repeated["result_id"] == resumed["result_id"] and repeated["model_calls"] == 0
    assert run_async(counts(thread)) == dict(results=int(choice == "approve"), receipts=int(choice == "approve"), decisions=1)


@pytest.mark.parametrize("point,choice", [("decision_saved", "approve"), ("decision_saved", "reject"),
    ("before_business_commit", "approve"), ("after_business_commit", "approve")])
def test_forced_process_exit_recovery(worker, input_file, point, choice):
    thread = thread_id()
    start = worker("start", thread, input=input_file)
    worker("decide", thread, proposal=start["proposal_id"], candidate_hash=start["candidate_hash"],
           request="request-" + uuid4().hex, choice=choice, crash_at=point, expect=75)
    query = worker("inspect", thread)
    committed = point == "after_business_commit"
    assert query["status"] == ("waiting" if point == "decision_saved" else "recoverable")
    assert query["business_committed"] == committed
    assert run_async(counts(thread)) == dict(results=int(committed), receipts=int(committed), decisions=1)
    resumed = worker("continue", thread)
    assert resumed["status"] == ("approved" if choice == "approve" else "rejected") and resumed["model_calls"] == 0
    assert resumed["proposal_id"] == start["proposal_id"]
    assert worker("continue", thread)["result_id"] == resumed["result_id"]
    assert run_async(counts(thread)) == dict(results=int(choice == "approve"), receipts=int(choice == "approve"), decisions=1)


def test_checkpoint_write_failure_is_raised_and_business_receipt_survives(payload, requirements, monkeypatch):
    async def scenario():
        thread = thread_id()
        async with open_service(MockAdapter(ModelReply(json.dumps(payload)))) as service:
            pending = await service.start(thread, requirements)
            original = type(service.saver).aput
            async def failing_put(saver, config, checkpoint, metadata, new_versions):
                if "outcome" in new_versions:
                    raise RuntimeError("injected_checkpoint_write_failure")
                return await original(saver, config, checkpoint, metadata, new_versions)
            # LangGraph caches an allowlist clone of the saver; patch the class.
            monkeypatch.setattr(type(service.saver), "aput", failing_put)
            with pytest.raises(RuntimeError, match="injected_checkpoint_write_failure"):
                await service.decide(thread, **decision_args(pending))
            assert await counts(thread) == dict(results=1, receipts=1, decisions=1)
            monkeypatch.setattr(type(service.saver), "aput", original)
        async with open_service(MockAdapter()) as service:
            recovered = await service.continue_run(thread)
            assert recovered["status"] == "approved"
            assert await counts(thread) == dict(results=1, receipts=1, decisions=1)
    run_async(scenario())


def test_operation_receipt_hash_conflict(payload, requirements):
    async def scenario():
        thread = thread_id()
        async with open_service(MockAdapter(ModelReply(json.dumps(payload)))) as service:
            pending = await service.start(thread, requirements)
            await service.decide(thread, **decision_args(pending))
            # Corrupt only this test's receipt inside a rollback-only transaction.
            class Rollback(Exception): pass
            with pytest.raises(Rollback):
                async with service.repository.conn.transaction():
                    await service.repository.conn.execute(
                        "UPDATE receipts SET request_hash=%s WHERE operation_id=%s", ("different", pending["operation_id"]))
                    with pytest.raises(WorkflowError, match="operation_id_conflict"):
                        await service.continue_run(thread)
                    raise Rollback()
            assert (await service.inspect(thread))["status"] == "approved"
    run_async(scenario())


def test_unknown_budget_approval_does_not_confirm_or_book(payload, requirements):
    async def scenario():
        payload["costs"][-1].update(amount_cents=None, category="unknown", unknown_reason="待查询门票")
        thread = thread_id()
        async with open_service(MockAdapter(ModelReply(json.dumps(payload)))) as service:
            pending = await service.start(thread, requirements)
            await service.decide(thread, **decision_args(pending))
            row = await (await service.repository.conn.execute(
                "SELECT payload FROM results WHERE operation_id=%s", (pending["operation_id"],))).fetchone()
            assert row["payload"]["budget"]["verification"] == "INDETERMINATE"
            assert row["payload"]["budget"]["total_cents"] is None
            assert row["payload"]["plan_confirmation"] == "UNCONFIRMED"
            assert row["payload"]["booked"] is False and row["payload"]["readiness"] == "NOT_ASSESSED"
    run_async(scenario())


@pytest.mark.parametrize("choice", ["approve", "reject"])
def test_first_decision_requires_real_approval_interrupt(payload, requirements, choice):
    async def scenario():
        thread = thread_id()
        async with open_service(MockAdapter(ModelReply(json.dumps(payload)))) as service:
            # Real PostgreSQL checkpoint after freeze, before entering dynamic wait.
            service.graph.interrupt_before_nodes = ["wait"]
            pending = await service.start(thread, requirements)
            assert pending["status"] == "recoverable" and not pending["approval_pending"]
            with pytest.raises(WorkflowError, match="approval_requires_waiting_state"):
                await service.decide(thread, **decision_args(pending, choice=choice))
            assert await counts(thread) == dict(results=0, receipts=0, decisions=0)
            service.graph.interrupt_before_nodes = []
            waiting = await service.continue_run(thread)
            assert waiting["status"] == "waiting" and waiting["approval_pending"]
            request = decision_args(waiting, choice=choice)
            result = await service.decide(thread, **request)
            assert result["status"] == ("approved" if choice == "approve" else "rejected")
            assert await service.decide(thread, **request) == result
    run_async(scenario())
