from copy import deepcopy
import json
from uuid import uuid4

import psycopg
import pytest

from easytravel.contracts import PlanDraftOutput
from easytravel.model_adapter import MockAdapter, ModelReply, OutputFailure
from easytravel.spike03.rules import ChangeError
from easytravel.spike03.service import open_service
from conftest import run_async


def uid(prefix="op"):
    return prefix + "-" + uuid4().hex


def adapter(value):
    raw = ModelReply(json.dumps(value))
    return MockAdapter(raw, raw)


def activity(payload, *, index=1, title="新活动说明", delta=0, resource=None):
    item = deepcopy(payload["items"][index])
    cost = deepcopy(payload["costs"][index])
    item["title"] = title
    if cost["amount_cents"] is not None:
        cost["amount_cents"] += delta
    if resource:
        item["resource_id"] = resource
        cost["query"]["resource_id"] = resource
    return dict(operation="replace_activity", target_item_id=item["id"], replacement=item, costs=[cost])


def hotel(payload, *, new_id=None):
    item, cost = deepcopy(payload["items"][0]), deepcopy(payload["costs"][0])
    item["resource_id"] = "hotel-002"
    cost["query"]["resource_id"] = "hotel-002"
    if new_id:
        item["id"] = new_id
        cost["item_id"] = new_id
    return dict(operation="replace_hotel", target_item_id="hotel", replacement=item, costs=[cost])


async def propose(service, trip, *operations, revision=3, **extra):
    return await service.propose_change(trip, adapter(dict(base_working_revision=revision,
        explanation="合成修改及影响说明", operations=list(operations), **extra)))


async def confirm(service, trip, **override):
    preview = await service.confirmation_preview(trip)
    request = dict(trip_id=trip, operation_id=uid(), expected_revision=preview["working_revision"],
        seen_disclosure_hash=preview["disclosure_hash"], explicit_confirmation=True) | override
    return await service.confirm_plan(**request), request


async def seed(service, payload, requirements, *, confirmed_ids=("hotel",), initial_snapshot=True):
    trip = uid("trip")
    await service.seed_working(trip, PlanDraftOutput.model_validate(payload), requirements,
                              revision=3, confirmed_item_ids=confirmed_ids)
    snapshot = None
    if initial_snapshot:
        result, _ = await confirm(service, trip)
        snapshot = await service.get_snapshot(result["latest_confirmed_version_id"])
    return trip, await service.get_working(trip), snapshot


async def capture(service, trip):
    working = await service.get_working(trip)
    snapshots = await (await service.conn.execute(
        "SELECT snapshot_id,data FROM confirmed_snapshots WHERE trip_id=%s ORDER BY snapshot_id", (trip,))).fetchall()
    receipts = await (await service.conn.execute(
        "SELECT operation_id,request_hash,result FROM operation_receipts WHERE trip_id=%s ORDER BY operation_id", (trip,))).fetchall()
    return dict(working=working.model_dump(mode="json"), snapshots=snapshots, receipts=receipts)


async def authorize(service, proposal):
    return await service.authorize_change(proposal.proposal_id, seen_candidate_hash=proposal.candidate_hash,
        item_ids=proposal.touched_item_ids, explicit_authorization=True)


@pytest.mark.parametrize("level", ["L1", "L2"])
def test_working_change_and_whole_confirmation_are_separate(payload, requirements, level):
    async def scenario():
        async with open_service() as service:
            trip, before, v1 = await seed(service, payload, requirements)
            op = activity(payload, resource="new-resource" if level == "L2" else None, delta=-1000 if level == "L2" else 0)
            proposal = await propose(service, trip, op)
            assert proposal.level == level
            request = dict(proposal_id=proposal.proposal_id, operation_id=uid())
            result = await service.apply_working_change(**request)
            for _ in range(10): assert await service.apply_working_change(**request) == result
            current = await service.get_working(trip)
            assert current.revision == 4 and current.latest_confirmed_version_id == v1.snapshot_id
            assert await service.get_snapshot(v1.snapshot_id) == v1
            assert (await service.status(trip))["plan_confirmation"] == "HAS_UNCONFIRMED_CHANGES"
            confirmed, confirm_request = await confirm(service, trip)
            for _ in range(10): assert await service.confirm_plan(**confirm_request) == confirmed
            v2 = await service.get_snapshot(confirmed["latest_confirmed_version_id"])
            assert v2.snapshot_id != v1.snapshot_id and v2.source_working_revision == 4
            assert v2.payload == current.payload and await service.get_snapshot(v1.snapshot_id) == v1
            assert len((await capture(service, trip))["snapshots"]) == 2
            assert (await service.status(trip))["plan_confirmation"] == "CONFIRMED"
    run_async(scenario())


@pytest.mark.parametrize("case", ["resource", "new_id", "room_type", "rooms", "dates", "delete", "delete_recreate"])
def test_confirmed_hotel_counterexamples_without_authorization(payload, requirements, case):
    async def scenario():
        async with open_service() as service:
            trip, before, v1 = await seed(service, payload, requirements)
            operation = hotel(payload, new_id="new-hotel" if case == "new_id" else None)
            if case not in {"resource", "new_id"}:
                operation["replacement"]["resource_id"] = "hotel-001"
                operation["costs"][0]["query"]["resource_id"] = "hotel-001"
            if case == "room_type":
                operation["replacement"]["room_type"] = "suite"
                operation["costs"][0]["query"]["room_type"] = "suite"
            if case == "rooms":
                operation["replacement"]["rooms"] = 2
                operation["costs"][0]["query"]["rooms"] = 2
            if case == "dates":
                operation["replacement"].update(start="2026-12-02T14:00:00+08:00", check_in="2026-12-02")
                operation["costs"][0]["query"]["start_date"] = "2026-12-02"
            operations = [operation]
            if case in {"delete", "delete_recreate"}:
                operations = [dict(operation="delete_item", target_item_id="hotel")]
                if case == "delete_recreate":
                    added = hotel(payload, new_id="recreated-hotel")
                    operations.append(dict(operation="add_item", item=added["replacement"], costs=added["costs"]))
            proposal = await propose(service, trip, *operations)
            assert proposal.level == "L3"
            saved = await capture(service, trip)
            with pytest.raises(ChangeError, match="specific_authorization_required"):
                await service.apply_working_change(proposal.proposal_id, operation_id=uid())
            assert await capture(service, trip) == saved
    run_async(scenario())


@pytest.mark.parametrize("new_id", [None, "new-hotel"])
def test_specific_hotel_authorization_and_confirmation_not_inherited(payload, requirements, new_id):
    async def scenario():
        async with open_service() as service:
            trip, old, v1 = await seed(service, payload, requirements)
            proposal = await propose(service, trip, hotel(payload, new_id=new_id))
            grant = await authorize(service, proposal)
            request = dict(proposal_id=proposal.proposal_id, operation_id=uid(), authorization_id=grant.authorization_id)
            result = await service.apply_working_change(**request)
            assert result["level"] == "L3" and result["latest_confirmed_version_id"] == v1.snapshot_id
            assert not (await service.get_working(trip)).item_confirmations
            assert await service.apply_working_change(**request) == result
            assert await service.get_snapshot(v1.snapshot_id) == v1
            await confirm(service, trip)
            status = await service.status(trip)
            assert status["confirmed_item_ids"] == [] and not status["booked"]
    run_async(scenario())


@pytest.mark.parametrize("case", ["other_hotel_candidate", "extra_activity", "scope", "hash", "no_consent"])
def test_hotel_authorization_is_specific(payload, requirements, case):
    async def scenario():
        async with open_service() as service:
            trip, _, _ = await seed(service, payload, requirements)
            first = await propose(service, trip, hotel(payload))
            if case in {"scope", "hash", "no_consent"}:
                saved = await capture(service, trip)
                with pytest.raises(ChangeError):
                    await service.authorize_change(first.proposal_id,
                        seen_candidate_hash="wrong" if case == "hash" else first.candidate_hash,
                        item_ids=("activity-0",) if case == "scope" else first.touched_item_ids,
                        explicit_authorization=case != "no_consent")
                assert await capture(service, trip) == saved
            else:
                grant = await authorize(service, first)
                changed = hotel(payload)
                changed["replacement"]["resource_id"] = "hotel-003"
                changed["costs"][0]["query"]["resource_id"] = "hotel-003"
                ops = [changed] if case == "other_hotel_candidate" else [hotel(payload), activity(payload)]
                second = await propose(service, trip, *ops)
                saved = await capture(service, trip)
                with pytest.raises(ChangeError, match="authorization_scope_mismatch"):
                    await service.apply_working_change(second.proposal_id, operation_id=uid(), authorization_id=grant.authorization_id)
                assert await capture(service, trip) == saved
    run_async(scenario())


@pytest.mark.parametrize("case", ["equal", "one_cent_over", "unknown", "known_over_with_unknown"])
def test_budget_recomputed_for_full_candidate(payload, requirements, case):
    async def scenario():
        async with open_service() as service:
            trip, _, v1 = await seed(service, payload, requirements)
            op = activity(payload, resource="new-resource", delta=1 if case == "one_cent_over" else 0)
            ops = [op]
            if case in {"unknown", "known_over_with_unknown"}:
                unknown = activity(payload, index=4)
                unknown["costs"][0].update(amount_cents=None, category="unknown", unknown_reason="待查询门票")
                ops.append(unknown)
                if case == "known_over_with_unknown": op["costs"][0]["amount_cents"] += 20000
            proposal = await propose(service, trip, *ops)
            saved = await capture(service, trip)
            if case in {"one_cent_over", "known_over_with_unknown"}:
                with pytest.raises(ChangeError, match="known_costs_exceed_hard_limit"):
                    await service.apply_working_change(proposal.proposal_id, operation_id=uid())
                assert await capture(service, trip) == saved
            else:
                result = await service.apply_working_change(proposal.proposal_id, operation_id=uid())
                if case == "equal": assert result["budget"]["total_cents"] == 134000
                else:
                    assert result["budget"]["total_cents"] is None
                    confirmation, _ = await confirm(service, trip)
                    assert confirmation["budget"]["verification"] == "INDETERMINATE"
                    assert confirmation["plan_confirmation"] == "CONFIRMED"
                    assert not confirmation["booked"] and confirmation["readiness"] == "NOT_ASSESSED"
    run_async(scenario())


@pytest.mark.parametrize("case", ["overlap", "outside_trip", "end_before_start", "naive", "hotel_disguised_as_activity", "delete_recreate_same_id"])
def test_time_and_type_bypasses_rejected(payload, requirements, case):
    async def scenario():
        async with open_service() as service:
            trip, _, _ = await seed(service, payload, requirements)
            saved = await capture(service, trip)
            op = activity(payload)
            if case == "overlap": op["replacement"].update(start="2026-12-02T11:30:00+08:00", end="2026-12-02T12:30:00+08:00")
            if case == "outside_trip":
                op["replacement"].update(start="2026-12-04T10:00:00+08:00", end="2026-12-04T11:00:00+08:00")
                op["costs"][0]["query"].update(start_date="2026-12-04", end_date="2026-12-04")
            if case == "end_before_start": op["replacement"]["end"] = op["replacement"]["start"]
            if case == "naive": op["replacement"]["start"] = "2026-12-02T10:00:00"
            if case == "hotel_disguised_as_activity":
                op["target_item_id"] = "hotel"
                op["replacement"]["id"] = "hotel"
                op["costs"][0]["item_id"] = "hotel"
            ops = [op]
            if case == "delete_recreate_same_id":
                ops = [dict(operation="delete_item", target_item_id="hotel"),
                       dict(operation="add_item", item=payload["items"][0], costs=[payload["costs"][0]])]
            with pytest.raises((ChangeError, OutputFailure, ValueError)):
                proposal = await propose(service, trip, *ops)
                await service.apply_working_change(proposal.proposal_id, operation_id=uid())
            assert await capture(service, trip) == saved
    run_async(scenario())


@pytest.mark.parametrize("point", ["after_working_update", "before_change_commit"])
def test_change_transaction_rolls_back_draft_pointer_receipt_and_authorization(payload, requirements, point):
    async def scenario():
        async with open_service() as service:
            trip, _, _ = await seed(service, payload, requirements)
            proposal = await propose(service, trip, hotel(payload))
            grant = await authorize(service, proposal)
            saved = await capture(service, trip)
            request = dict(proposal_id=proposal.proposal_id, operation_id=uid(), authorization_id=grant.authorization_id)
        def fail(at):
            if at == point: raise RuntimeError("injected_change_failure")
        async with open_service(fault=fail) as service:
            with pytest.raises(RuntimeError, match="injected_change_failure"):
                await service.apply_working_change(**request)
            assert await capture(service, trip) == saved
            assert (await service.repository.authorization(grant.authorization_id))[1] is None
        async with open_service() as service:
            assert (await service.apply_working_change(**request))["working_revision"] == 4
    run_async(scenario())


@pytest.mark.parametrize("point", ["after_snapshot_insert", "after_confirmation_pointer", "before_confirmation_commit"])
def test_confirmation_transaction_rolls_back_snapshot_pointer_receipt(payload, requirements, point):
    async def scenario():
        async with open_service() as service:
            trip, _, _ = await seed(service, payload, requirements)
            proposal = await propose(service, trip, activity(payload))
            await service.apply_working_change(proposal.proposal_id, operation_id=uid())
            preview = await service.confirmation_preview(trip)
            request = dict(trip_id=trip, operation_id=uid(), expected_revision=4,
                seen_disclosure_hash=preview["disclosure_hash"], explicit_confirmation=True)
            saved = await capture(service, trip)
        def fail(at):
            if at == point: raise RuntimeError("injected_confirmation_failure")
        async with open_service(fault=fail) as service:
            with pytest.raises(RuntimeError, match="injected_confirmation_failure"):
                await service.confirm_plan(**request)
            assert await capture(service, trip) == saved
        async with open_service() as service:
            assert (await service.confirm_plan(**request))["working_revision"] == 4
    run_async(scenario())


def test_stale_proposal_and_authorization_never_overwrite_new_revision(payload, requirements):
    async def scenario():
        async with open_service() as service:
            trip, _, _ = await seed(service, payload, requirements)
            stale = await propose(service, trip, hotel(payload))
            grant = await authorize(service, stale)
            current = await propose(service, trip, activity(payload))
            await service.apply_working_change(current.proposal_id, operation_id=uid())
            saved = await capture(service, trip)
            with pytest.raises(ChangeError, match="stale_proposal"):
                await service.apply_working_change(stale.proposal_id, operation_id=uid(), authorization_id=grant.authorization_id)
            assert await capture(service, trip) == saved
            with pytest.raises(ChangeError, match="stale_proposal"):
                await propose(service, trip, activity(payload, title="旧修订"), revision=3)
    run_async(scenario())


@pytest.mark.parametrize("mutation", ["patch", "change_budget", "change_dates", "change_destination", "level", "authorization", "confirmed"])
def test_model_cannot_set_authority_or_unsupported_operations(payload, requirements, mutation):
    async def scenario():
        async with open_service() as service:
            trip, _, _ = await seed(service, payload, requirements)
            op = activity(payload)
            extra = {}
            if mutation in {"level", "authorization", "confirmed"}: extra[mutation] = "forged"
            else: op["operation"] = mutation
            saved = await capture(service, trip)
            with pytest.raises(OutputFailure): await propose(service, trip, op, **extra)
            assert await capture(service, trip) == saved
    run_async(scenario())


@pytest.mark.parametrize("mutation", ["revision", "hash", "no_consent"])
def test_confirmation_requires_current_revision_and_disclosure(payload, requirements, mutation):
    async def scenario():
        async with open_service() as service:
            trip, _, _ = await seed(service, payload, requirements)
            saved = await capture(service, trip)
            with pytest.raises(ChangeError):
                await confirm(service, trip, **({"expected_revision":2} if mutation == "revision" else
                    {"seen_disclosure_hash":"wrong"} if mutation == "hash" else {"explicit_confirmation":False}))
            assert await capture(service, trip) == saved
    run_async(scenario())


@pytest.mark.parametrize("statement", ["UPDATE confirmed_snapshots SET data=data WHERE snapshot_id=%s", "DELETE FROM confirmed_snapshots WHERE snapshot_id=%s"])
def test_snapshot_immutability_enforced_by_postgres(payload, requirements, statement):
    async def scenario():
        async with open_service() as service:
            trip, _, v1 = await seed(service, payload, requirements)
            with pytest.raises(psycopg.Error) as error: await service.conn.execute(statement, (v1.snapshot_id,))
            assert error.value.sqlstate == "55000"
            assert await service.get_snapshot(v1.snapshot_id) == v1
    run_async(scenario())


def test_receipt_precedes_stale_checks_and_conflicts_across_operation_types(payload, requirements):
    async def scenario():
        async with open_service() as service:
            trip, _, _ = await seed(service, payload, requirements)
            first = await propose(service, trip, activity(payload))
            request = dict(proposal_id=first.proposal_id, operation_id=uid())
            result = await service.apply_working_change(**request)
            changed = (await service.get_working(trip)).payload.model_dump(mode="json")
            second = await propose(service, trip, activity(changed, title="再次修改"), revision=4)
            with pytest.raises(ChangeError, match="operation_id_conflict"):
                await service.apply_working_change(second.proposal_id, operation_id=request["operation_id"])
            await service.apply_working_change(second.proposal_id, operation_id=uid())
            assert await service.apply_working_change(**request) == result
            assert (await service.get_working(trip)).revision == 5
            with pytest.raises(ChangeError, match="operation_id_conflict"):
                await confirm(service, trip, operation_id=request["operation_id"])
    run_async(scenario())


def test_overall_confirmation_does_not_confirm_hotel_or_create_duplicate_snapshot(payload, requirements):
    async def scenario():
        async with open_service() as service:
            trip, _, _ = await seed(service, payload, requirements, confirmed_ids=(), initial_snapshot=False)
            assert (await service.status(trip))["plan_confirmation"] == "UNCONFIRMED"
            one, _ = await confirm(service, trip)
            two, _ = await confirm(service, trip)
            assert one["latest_confirmed_version_id"] == two["latest_confirmed_version_id"]
            status = await service.status(trip)
            assert status["plan_confirmation"] == "CONFIRMED" and status["confirmed_item_ids"] == []
            assert not status["booked"] and len((await capture(service, trip))["snapshots"]) == 1
    run_async(scenario())


@pytest.mark.parametrize("method", ["apply", "confirm", "authorize", "read"])
def test_wrong_actor_never_changes_business_state(payload, requirements, method):
    async def scenario():
        async with open_service() as service:
            trip, _, _ = await seed(service, payload, requirements)
            proposal = await propose(service, trip, hotel(payload))
            saved = await capture(service, trip)
            with pytest.raises(ChangeError, match="wrong_actor"):
                if method == "apply": await service.apply_working_change(proposal.proposal_id, operation_id=uid(), actor="other")
                if method == "confirm": await confirm(service, trip, actor="other")
                if method == "authorize": await service.authorize_change(proposal.proposal_id, seen_candidate_hash=proposal.candidate_hash,
                    item_ids=proposal.touched_item_ids, explicit_authorization=True, actor="other")
                if method == "read": await service.get_working(trip, actor="other")
            assert await capture(service, trip) == saved
    run_async(scenario())


@pytest.mark.parametrize("kind", ["apply_working_change", "confirm_plan"])
def test_commit_then_process_exit_and_retry_is_idempotent(payload, requirements, business_worker, kind):
    async def prepare():
        async with open_service() as service:
            trip, _, v1 = await seed(service, payload, requirements)
            proposal = await propose(service, trip, activity(payload))
            if kind == "apply_working_change":
                request = dict(proposal_id=proposal.proposal_id, operation_id=uid())
            else:
                await service.apply_working_change(proposal.proposal_id, operation_id=uid())
                preview = await service.confirmation_preview(trip)
                request = dict(trip_id=trip, operation_id=uid(), expected_revision=4,
                    seen_disclosure_hash=preview["disclosure_hash"], explicit_confirmation=True)
            return trip, v1, request
    trip, v1, request = run_async(prepare())
    business_worker(kind, request, crash_at="after_change_commit" if kind == "apply_working_change" else "after_confirmation_commit", expect=75)
    one = business_worker(kind, request)
    two = business_worker(kind, request)
    assert one["result"] == two["result"] and one["pid"] != two["pid"]
    async def verify():
        async with open_service() as service:
            saved = await capture(service, trip)
            assert saved["working"]["revision"] == 4
            assert len(saved["snapshots"]) == (1 if kind == "apply_working_change" else 2)
            assert await service.get_snapshot(v1.snapshot_id) == v1
            assert len([r for r in saved["receipts"] if r["operation_id"] == request["operation_id"]]) == 1
    run_async(verify())


def test_specific_authorization_cannot_override_hard_budget(payload, requirements):
    async def scenario():
        async with open_service() as service:
            trip, _, _ = await seed(service, payload, requirements)
            op = hotel(payload)
            op["costs"][0]["amount_cents"] += 1
            proposal = await propose(service, trip, op)
            grant = await authorize(service, proposal)
            saved = await capture(service, trip)
            with pytest.raises(ChangeError, match="known_costs_exceed_hard_limit"):
                await service.apply_working_change(proposal.proposal_id, operation_id=uid(), authorization_id=grant.authorization_id)
            assert await capture(service, trip) == saved
            assert (await service.repository.authorization(grant.authorization_id))[1] is None
    run_async(scenario())


def test_authorization_for_one_hotel_does_not_authorize_another(payload, requirements):
    async def scenario():
        async with open_service() as service:
            second = hotel(payload, new_id="second-hotel")
            second["costs"][0]["id"] = "second-hotel-cost"
            second["costs"][0]["amount_cents"] = 1000
            payload["items"].append(second["replacement"])
            payload["costs"].append(second["costs"][0])
            payload["costs"][0]["amount_cents"] -= 1000
            trip, _, _ = await seed(service, payload, requirements, confirmed_ids=("hotel", "second-hotel"))
            first_proposal = await propose(service, trip, hotel(payload))
            grant = await authorize(service, first_proposal)
            replace_second = deepcopy(second)
            replace_second["target_item_id"] = "second-hotel"
            replace_second["replacement"]["resource_id"] = "another-hotel"
            replace_second["costs"][0]["query"]["resource_id"] = "another-hotel"
            second_proposal = await propose(service, trip, replace_second)
            saved = await capture(service, trip)
            with pytest.raises(ChangeError, match="authorization_scope_mismatch"):
                await service.apply_working_change(second_proposal.proposal_id, operation_id=uid(), authorization_id=grant.authorization_id)
            assert await capture(service, trip) == saved
    run_async(scenario())


def test_whole_confirmation_does_not_authorize_l3_hotel_change(payload, requirements):
    async def scenario():
        async with open_service() as service:
            trip, _, _ = await seed(service, payload, requirements)
            proposal = await propose(service, trip, hotel(payload))
            await confirm(service, trip)  # Explicit overall acceptance is not item-change authorization.
            with pytest.raises(ChangeError, match="specific_authorization_required"):
                await service.apply_working_change(proposal.proposal_id, operation_id=uid())
    run_async(scenario())


def test_unknown_disclosure_must_match_the_content_seen(payload, requirements):
    async def scenario():
        payload["costs"][1].update(amount_cents=None, category="unknown", unknown_reason="未知门票")
        async with open_service() as service:
            trip, _, _ = await seed(service, payload, requirements)
            old_preview = await service.confirmation_preview(trip)
            assert old_preview["unknown_costs"] == [dict(id="cost-1", reason="未知门票")]
            op = activity(payload)
            op["costs"][0]["unknown_reason"] = "未知预约费"
            proposal = await propose(service, trip, op)
            await service.apply_working_change(proposal.proposal_id, operation_id=uid())
            saved = await capture(service, trip)
            with pytest.raises(ChangeError, match="disclosure_not_acknowledged"):
                await service.confirm_plan(trip, operation_id=uid(), expected_revision=4,
                    seen_disclosure_hash=old_preview["disclosure_hash"], explicit_confirmation=True)
            assert await capture(service, trip) == saved
    run_async(scenario())


def test_confirmed_activity_cannot_take_the_unconfirmed_l1_path(payload, requirements):
    async def scenario():
        async with open_service() as service:
            trip, _, _ = await seed(service, payload, requirements, confirmed_ids=("hotel", "activity-0"))
            proposal = await propose(service, trip, activity(payload))
            assert proposal.level == "L3"
            with pytest.raises(ChangeError, match="specific_authorization_required"):
                await service.apply_working_change(proposal.proposal_id, operation_id=uid())
    run_async(scenario())
