"""Deterministic facts and confirmation eligibility; no writes or booking side effects."""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Literal, Mapping

from .contracts import Evidence, PlanDraftOutput, TripRequirements


@dataclass(frozen=True)
class BudgetResult:
    known_subtotal_cents: int
    total_cents: int | None
    unknown_cost_ids: tuple[str, ...]
    verification: Literal["VERIFIED", "ESTIMATED", "INDETERMINATE"]
    constraint: Literal["WITHIN_LIMIT", "OVER_LIMIT", "UNDETERMINED"]
    verified_cost_ids: tuple[str, ...]
    evidence_issues: tuple[tuple[str, str], ...]


def validate_trip_scope(draft: PlanDraftOutput, requirements: TripRequirements) -> None:
    for item in draft.items:
        if not (requirements.start_date <= item.start.date() <= item.end.date() <= requirements.end_date):
            raise ValueError("item outside trip dates")


def evaluate_budget(draft: PlanDraftOutput, requirements: TripRequirements,
                    evidence: Mapping[str, Evidence], *, now: datetime) -> BudgetResult:
    """Evidence mapping MUST come from the server, never model-produced records."""
    if now.utcoffset() is None:
        raise ValueError("aware evaluation time required")
    validate_trip_scope(draft, requirements)
    known = sum(c.amount_cents for c in draft.costs if c.amount_cents is not None)
    unknown = tuple(c.id for c in draft.costs if c.amount_cents is None)
    verified, issues = [], []
    for cost in draft.costs:
        record = evidence.get(cost.evidence_id) if cost.evidence_id else None
        if cost.amount_cents is None:
            reason = "unknown_price"
        elif cost.category != "quote":
            reason = "estimate"
        elif cost.is_mock:
            reason = "mock_cost"
        elif record is None or record.id != cost.evidence_id:
            reason = "missing_or_mismatched_evidence_id"
        elif record.source_type == "mock":
            reason = "mock_evidence"
        elif record.query != cost.query:
            reason = "query_mismatch"
        elif record.amount_cents != cost.amount_cents:
            reason = "amount_mismatch"
        elif not record.obtained_at <= now <= record.valid_until:
            reason = "evidence_not_current"
        else:
            verified.append(cost.id)
            continue
        issues.append((cost.id, reason))
    verification = "INDETERMINATE" if unknown else (
        "VERIFIED" if len(verified) == len(draft.costs) else "ESTIMATED")
    constraint = "OVER_LIMIT" if known > requirements.hard_limit_cents else (
        "UNDETERMINED" if unknown else "WITHIN_LIMIT")
    return BudgetResult(known, None if unknown else known, unknown, verification,
                        constraint, tuple(verified), tuple(issues))


def disclosure_hash(draft: PlanDraftOutput, requirements: TripRequirements,
                    working_revision: int, budget: BudgetResult) -> str:
    payload = dict(draft=draft.model_dump(mode="json"),
                   requirements=requirements.model_dump(mode="json"),
                   working_revision=working_revision,
                   budget=budget.__dict__)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class ConfirmationAssessment:
    allowed: bool
    reason: str
    plan_confirmation: Literal["UNCONFIRMED", "CONFIRMED"]
    budget: BudgetResult
    disclosure_hash: str
    readiness: Literal["NOT_ASSESSED"] = "NOT_ASSESSED"
    booked: bool = False
    # This is only a rule assessment, never a persisted version or booking.


def assess_confirmation(draft: PlanDraftOutput, requirements: TripRequirements,
                        evidence: Mapping[str, Evidence], *, now: datetime,
                        working_revision: int, seen_revision: int,
                        seen_disclosure_hash: str, explicit_confirmation: bool) -> ConfirmationAssessment:
    if type(working_revision) is not int or working_revision < 1:
        raise ValueError("positive working revision required")
    budget = evaluate_budget(draft, requirements, evidence, now=now)
    digest = disclosure_hash(draft, requirements, working_revision, budget)
    if budget.constraint == "OVER_LIMIT":
        reason = "known_costs_exceed_hard_limit"
    elif explicit_confirmation is not True:
        reason = "explicit_confirmation_required"
    elif type(seen_revision) is not int or seen_revision != working_revision:
        reason = "stale_revision"
    elif seen_disclosure_hash != digest:
        reason = "disclosure_not_acknowledged"
    else:
        reason = "eligible"
    allowed = reason == "eligible"
    return ConfirmationAssessment(allowed, reason, "CONFIRMED" if allowed else "UNCONFIRMED",
                                  budget, digest)
