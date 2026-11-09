import asyncio
from datetime import datetime
import json

from pydantic import ValidationError
import pytest

from easytravel.application import generate_draft
from easytravel.contracts import ChangeProposalOutput, Evidence, PlanDraftOutput, TripRequirements
from easytravel.model_adapter import MockAdapter, ModelReply, OutputFailure, generate_output, parse_output
from easytravel.rules import assess_confirmation, disclosure_hash, evaluate_budget

NOW = datetime.fromisoformat("2026-11-09T12:00:00+08:00")


def parse(payload):
    return parse_output(json.dumps(payload), PlanDraftOutput)


def trusted(payload):
    records = {}
    for cost in payload["costs"]:
        cost["is_mock"] = False
        records[cost["evidence_id"]] = Evidence.model_validate(dict(
            id=cost["evidence_id"], source_type="supplier_quote", query=cost["query"],
            amount_cents=cost["amount_cents"], obtained_at=NOW,
            valid_until=datetime.fromisoformat("2026-11-10T12:00:00+08:00")))
    return records


def test_valid_end_to_end(payload, requirements):
    result = asyncio.run(generate_draft(MockAdapter(ModelReply(json.dumps(payload))), requirements, {}, now=NOW))
    assert result.budget.known_subtotal_cents == 134000
    assert result.budget.total_cents == 134000
    assert result.budget.verification == "ESTIMATED"
    assert result.budget.constraint == "WITHIN_LIMIT"


@pytest.mark.parametrize("bad", [-1, True, 1.5, "60000", 10**12+1])
def test_strict_money(payload, bad):
    payload["costs"][0]["amount_cents"] = bad
    with pytest.raises(ValidationError): parse(payload)


@pytest.mark.parametrize("field,value", [("confirmed", True), ("version", 2),
    ("budget_approved", True), ("verification", "VERIFIED"), ("level", "L1"),
    ("evidence", []), ("readiness", "READY"), ("booked", True)])
def test_forged_authority(payload, field, value):
    payload[field] = value
    with pytest.raises(ValidationError): parse(payload)


@pytest.mark.parametrize("case", ["missing", "item_id", "cost_id", "reference", "query", "verified_price",
                                  "hotel", "time", "naive", "unknown", "uncovered", "too_many"])
def test_invalid_contract(payload, case):
    if case == "missing": del payload["costs"][0]["amount_cents"]
    if case == "item_id": payload["items"][1]["id"] = "hotel"
    if case == "cost_id": payload["costs"][1]["id"] = "cost-0"
    if case == "reference": payload["costs"][0]["item_id"] = "absent"
    if case == "query": payload["costs"][0]["query"]["rooms"] = 2
    if case == "verified_price": payload["costs"][0]["verified"] = True
    if case == "hotel": del payload["items"][0]["rooms"]
    if case == "time": payload["items"][1]["end"] = payload["items"][1]["start"]
    if case == "naive": payload["items"][1]["start"] = "2026-12-02T10:00:00"
    if case == "unknown": payload["costs"][0]["amount_cents"] = None
    if case == "uncovered": payload["costs"].pop()
    if case == "too_many": payload["items"] *= 101
    with pytest.raises(ValidationError): parse(payload)


@pytest.mark.parametrize("raw,code", [('{' ,'invalid_json'), ('{"items":[],"items":[]}', 'invalid_json'),
    ('{"costs": NaN}', 'invalid_json'), ('{}','schema_error'),
    ('{"items": 1, "costs": []}', 'schema_error')])
def test_parse_failure_bounded(raw, code):
    adapter = MockAdapter(ModelReply(raw), ModelReply(raw), ModelReply("{}"))
    with pytest.raises(OutputFailure) as failure:
        asyncio.run(generate_output(adapter, PlanDraftOutput))
    assert (failure.value.code, failure.value.attempts, adapter.calls) == (code, 2, 2)


def test_one_repair_success(payload):
    adapter = MockAdapter(ModelReply('{'), ModelReply(json.dumps(payload)))
    assert asyncio.run(generate_output(adapter, PlanDraftOutput)) == parse(payload)
    assert adapter.calls == 2


@pytest.mark.parametrize("response,code", [(ModelReply(refused=True), "refusal"),
    (TimeoutError("secret"), "timeout"), (RuntimeError("secret"), "adapter_error")])
def test_nonrepairable_failure(response, code):
    adapter = MockAdapter(response)
    with pytest.raises(OutputFailure) as failure:
        asyncio.run(generate_output(adapter, PlanDraftOutput))
    assert failure.value.code == code and adapter.calls == 1
    assert "secret" not in str(failure.value)


def test_actual_deadline():
    class Slow:
        async def generate(self, *, repair):
            await asyncio.sleep(10)
            return ModelReply('{}')
    with pytest.raises(OutputFailure, match="timeout"):
        asyncio.run(generate_output(Slow(), PlanDraftOutput, timeout_seconds=.01))


@pytest.mark.parametrize("delta,unknown,expected", [(0, False, "WITHIN_LIMIT"),
    (1, False, "OVER_LIMIT"), (0, True, "UNDETERMINED"), (20000, True, "OVER_LIMIT")])
def test_budget_edges(payload, requirements, delta, unknown, expected):
    payload["costs"][0]["amount_cents"] += delta
    if unknown:
        payload["costs"][-1].update(amount_cents=None, category="unknown", unknown_reason="待查询")
    result = evaluate_budget(parse(payload), requirements, {}, now=NOW)
    assert result.constraint == expected
    assert result.total_cents == (None if unknown else 134000+delta)
    assert result.verification == ("INDETERMINATE" if unknown else "ESTIMATED")


@pytest.mark.parametrize("case", ["valid", "missing", "id", "dates", "resource", "rooms", "currency",
    "amount", "mock", "expired", "future", "estimate", "model_mock_false"])
def test_server_evidence(payload, requirements, case):
    records = trusted(payload)
    record = records["evidence-0"].model_dump()
    if case == "missing": records.pop("evidence-0")
    if case == "id": record["id"] = "other"
    if case == "dates": record["query"]["start_date"] = "2026-11-30"
    if case == "resource": record["query"]["resource_id"] = "other"
    if case == "rooms": record["query"]["rooms"] = 2
    if case == "currency":
        record["query"]["currency"] = "USD"
        with pytest.raises(ValidationError): Evidence.model_validate(record)
        return
    if case == "amount": record["amount_cents"] += 1
    if case in ("mock", "model_mock_false"): record["source_type"] = "mock"
    if case == "expired":
        record.update(obtained_at="2026-11-01T00:00:00+08:00", valid_until="2026-11-02T00:00:00+08:00")
    if case == "future":
        record.update(obtained_at="2026-11-10T00:00:00+08:00", valid_until="2026-11-11T00:00:00+08:00")
    if case == "estimate": payload["costs"][0]["category"] = "estimate"
    if case != "missing": records["evidence-0"] = Evidence.model_validate(record)
    result = evaluate_budget(parse(payload), requirements, records, now=NOW)
    assert result.verification == ("VERIFIED" if case == "valid" else "ESTIMATED")
    assert ("cost-0" in result.verified_cost_ids) == (case == "valid")


@pytest.mark.parametrize("case", ["unknown", "over", "no_consent", "stale", "hash", "changed_reason", "changed_limit"])
def test_confirmation_separation(payload, requirements, case):
    payload["costs"][-1].update(amount_cents=None, category="unknown", unknown_reason="待查询门票")
    if case == "over": payload["costs"][0]["amount_cents"] += 20000
    draft = parse(payload)
    budget = evaluate_budget(draft, requirements, {}, now=NOW)
    digest = disclosure_hash(draft, requirements, 3, budget)
    if case == "changed_reason":
        payload["costs"][-1]["unknown_reason"] = "另一未知费用"
        draft = parse(payload)
    if case == "changed_limit":
        data = requirements.model_dump()
        data["hard_limit_cents"] += 1
        requirements = TripRequirements.model_validate(data)
    result = assess_confirmation(draft, requirements, {}, now=NOW, working_revision=3,
        seen_revision=2 if case == "stale" else 3, seen_disclosure_hash="wrong" if case == "hash" else digest,
        explicit_confirmation=case != "no_consent")
    assert result.allowed == (case == "unknown")
    assert result.budget.verification == "INDETERMINATE"
    assert result.readiness == "NOT_ASSESSED" and result.booked is False
    assert result.plan_confirmation == ("CONFIRMED" if case == "unknown" else "UNCONFIRMED")


def test_proposal_shared_path(payload):
    proposal = dict(base_working_revision=3, explanation="替换活动候选", operations=[dict(
        operation="replace_activity", target_item_id="activity-0", replacement=payload["items"][1],
        costs=[payload["costs"][1]])])
    result = asyncio.run(generate_output(MockAdapter(ModelReply(json.dumps(proposal))), ChangeProposalOutput))
    assert result.operations[0].target_item_id == "activity-0"
    for mutation in [dict(level="L1"), dict(confirmed=True), dict(budget_approved=True)]:
        with pytest.raises(ValidationError): ChangeProposalOutput.model_validate(proposal | mutation)
    proposal["operations"][0]["operation"] = "patch"
    with pytest.raises(ValidationError): ChangeProposalOutput.model_validate(proposal)


def test_trip_scope_and_requirements(payload, requirements):
    data = requirements.model_dump()
    for mutation in [dict(hard_limit_cents=True), dict(end_date="2026-11-30"), dict(timezone="UTC")]:
        with pytest.raises(ValidationError): TripRequirements.model_validate(data | mutation)
    data["end_date"] = "2026-12-02"
    with pytest.raises(ValueError, match="outside trip"):
        evaluate_budget(parse(payload), TripRequirements.model_validate(data), {}, now=NOW)


@pytest.mark.parametrize("case,kind", [("missing", "missing"), ("type", "int_type")])
def test_safe_schema_diagnostics(payload, case, kind):
    if case == "missing":
        del payload["costs"][0]["amount_cents"]
    else:
        payload["costs"][0]["amount_cents"] = "private-model-value"
    reply = ModelReply(json.dumps(payload))
    with pytest.raises(OutputFailure) as failure:
        asyncio.run(generate_output(MockAdapter(reply, reply), PlanDraftOutput))
    assert failure.value.code == "schema_error"
    assert (("costs", 0, "amount_cents"), kind) in failure.value.validation_issues
    assert "private-model-value" not in repr(failure.value.validation_issues)


@pytest.mark.parametrize("mode", ["verified", "estimated", "verified_over_limit"])
def test_confirmation_preserves_known_facts(payload, requirements, mode):
    if mode == "verified_over_limit": payload["costs"][0]["amount_cents"] += 1
    records = {} if mode == "estimated" else trusted(payload)
    draft = parse(payload)
    before = evaluate_budget(draft, requirements, records, now=NOW)
    result = assess_confirmation(draft, requirements, records, now=NOW,
        working_revision=1, seen_revision=1, explicit_confirmation=True,
        seen_disclosure_hash=disclosure_hash(draft, requirements, 1, before))
    assert result.budget == before
    assert result.allowed == (mode != "verified_over_limit")
    assert not result.booked and result.readiness == "NOT_ASSESSED"


@pytest.mark.parametrize("case", ["empty", "long_title", "mock_string", "zero_rooms", "activity_hotel_fields",
                                  "unknown_zero", "wrong_offset", "evidence_in_item"])
def test_more_schema_boundaries(payload, case):
    if case == "empty": payload["items"] = []
    if case == "long_title": payload["items"][0]["title"] = "a" * 501
    if case == "mock_string": payload["costs"][0]["is_mock"] = "false"
    if case == "zero_rooms": payload["items"][0]["rooms"] = 0
    if case == "activity_hotel_fields": payload["items"][1]["rooms"] = 1
    if case == "unknown_zero": payload["costs"][0].update(category="unknown", unknown_reason="未知")
    if case == "wrong_offset": payload["items"][0]["start"] = "2026-12-01T06:00:00Z"
    if case == "evidence_in_item": payload["items"][0]["evidence"] = {"verified": True}
    with pytest.raises(ValidationError): parse(payload)


def test_no_repair_after_refusal(payload):
    adapter = MockAdapter(ModelReply('{'), ModelReply(refused=True), ModelReply(json.dumps(payload)))
    with pytest.raises(OutputFailure) as failure:
        asyncio.run(generate_output(adapter, PlanDraftOutput))
    assert failure.value.code == "refusal" and adapter.calls == 2


def test_evidence_models_and_schema_export():
    from easytravel.contracts import CostLine, PlanItem, PriceQuery
    for schema in [TripRequirements, PlanItem, CostLine, Evidence, PlanDraftOutput, ChangeProposalOutput]:
        assert schema.model_json_schema()["additionalProperties"] is False
    with pytest.raises(ValidationError):
        PriceQuery(resource_id="r", start_date="2026-12-03", end_date="2026-12-01",
                   currency="CNY", room_type=None, rooms=None)


@pytest.mark.parametrize("case", ["hotel", "new_id", "duplicate", "hard_budget", "destination"])
def test_proposal_restrictions(payload, case):
    operation = dict(operation="replace_activity", target_item_id="activity-0",
                     replacement=payload["items"][1], costs=[payload["costs"][1]])
    proposal = dict(base_working_revision=3, explanation="候选", operations=[operation])
    if case == "hotel": operation["replacement"] = payload["items"][0]
    if case == "new_id": operation["replacement"]["id"] = "new"
    if case == "duplicate": proposal["operations"].append(operation)
    if case in ("hard_budget", "destination"): operation["operation"] = f"change_{case}"
    with pytest.raises(ValidationError): ChangeProposalOutput.model_validate(proposal)
