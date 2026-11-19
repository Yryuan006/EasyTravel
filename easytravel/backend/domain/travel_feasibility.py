"""Synthetic F03 arithmetic only. No routing, service policy, or readiness authority."""
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .places import Coordinates, Identifier, PlaceContract, ResourceRef, Text
from .opening_feasibility import LocalMinute, OpeningSource, Status, aggregate_status

RULE_VERSION = "F03-synthetic-v1"
BUFFER_VERSION = "M2-00-city-max600-ceil20pct-minute-v1"
Seconds = Annotated[int, Field(ge=0, le=86400)]


class LegEndpoint(PlaceContract):
    resource: ResourceRef
    access_id: Identifier
    role: Literal["exit", "entrance"]
    coordinates: Coordinates | None = None  # Descriptive only; never used in arithmetic.


class LegDuration(PlaceContract):
    origin_walk_seconds: Seconds | None
    destination_walk_seconds: Seconds | None
    waiting_seconds: Seconds | None
    transfer_seconds: Seconds | None
    riding_seconds: Seconds | None
    complete: bool
    extra_buffer_included: Literal[False] = False

    def total(self) -> int | None:
        values = (self.origin_walk_seconds, self.destination_walk_seconds,
                  self.waiting_seconds, self.transfer_seconds, self.riding_seconds)
        return sum(values) if self.complete and all(v is not None for v in values) else None

    @model_validator(mode="after")
    def coherent(self):
        if self.complete and (self.total() is None or not 0 < self.total() <= 86400):
            raise ValueError("complete duration requires all components and positive bounded total")
        return self


class LegApplicableScope(PlaceContract):
    start_date: date
    end_date: date
    window_start: LocalMinute
    window_end: LocalMinute
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"

    @model_validator(mode="after")
    def ordered(self):
        if self.end_date < self.start_date or self.window_start >= self.window_end:
            raise ValueError("ordered dates and positive same-day time window required")
        return self


class TravelLegSource(OpeningSource):
    provider: Literal["synthetic_travel_fixture"] = "synthetic_travel_fixture"
    source_reference: Annotated[str, Field(pattern=r"^synthetic-travel:[A-Za-z0-9_-]+$", max_length=100)]


class TravelLegFacts(PlaceContract):
    schema_version: Literal["travel-leg-facts-v1"] = "travel-leg-facts-v1"
    fact_id: Identifier
    leg_id: Identifier
    from_exit: LegEndpoint
    to_entrance: LegEndpoint
    duration: LegDuration | None
    applicable_scope: LegApplicableScope
    source: TravelLegSource
    note: Text | None = None

    @model_validator(mode="after")
    def directed(self):
        if self.from_exit.role != "exit" or self.to_entrance.role != "entrance":
            raise ValueError("explicit exit to entrance required")
        return self


class TravelActivity(PlaceContract):
    item_id: Identifier
    resource: ResourceRef
    start: datetime
    end: datetime


class ConsecutiveActivityPair(PlaceContract):
    pair_id: Identifier
    leg_id: Identifier
    previous: TravelActivity
    next: TravelActivity
    from_exit: LegEndpoint
    to_entrance: LegEndpoint
    policy_scope: Literal["same_city", "rail_air_policy_required"] = "same_city"


class F03ValidationInput(PlaceContract):
    # Internal application DTO. Registry must be supplied by server resolution,
    # never extracted from untrusted text or used as public caller authorization.
    resolved_endpoints: Annotated[tuple[LegEndpoint, ...], Field(max_length=200)]
    pairs: Annotated[tuple[ConsecutiveActivityPair, ...], Field(max_length=100)]
    facts: Annotated[tuple[TravelLegFacts, ...], Field(max_length=200)]

    @model_validator(mode="after")
    def unique(self):
        for keys in ([p.pair_id for p in self.pairs], [f.fact_id for f in self.facts],
                     [(e.resource.internal_resource_id, e.access_id, e.role) for e in self.resolved_endpoints]):
            if len(keys) != len(set(keys)):
                raise ValueError("duplicate identifiers")
        resources = {}
        for endpoint in self.resolved_endpoints:
            key = endpoint.resource.internal_resource_id
            if key in resources and resources[key] != endpoint.resource:
                raise ValueError("conflicting server resource bindings")
            resources[key] = endpoint.resource
        return self


Reason = Literal["invalid_time", "activity_overlap", "cross_day_unsupported", "endpoint_binding_mismatch",
                 "missing_matching_leg", "date_not_applicable", "time_window_not_applicable",
                 "source_expired", "observation_in_future", "duration_incomplete",
                 "source_conflict", "policy_scope_unsupported", "time_sufficient", "time_insufficient"]


class F03FeasibilityCheck(PlaceContract):
    check_id: Literal["F03"] = "F03"
    pair_id: Identifier
    leg_id: Identifier
    item_ids: tuple[Identifier, Identifier]
    from_exit: LegEndpoint
    to_entrance: LegEndpoint
    target_departure: datetime
    target_next_start: datetime
    status: Status
    reason_code: Reason
    fact_ids: tuple[Identifier, ...]
    duration_seconds: Seconds | None = None
    extra_buffer_seconds: Seconds | None = None
    required_next_start: datetime | None = None
    severity: Literal["info", "blocker"]
    action: Literal["none", "revise_activities", "obtain_facts"]
    rule_version: Literal["F03-synthetic-v1"] = RULE_VERSION
    buffer_rule_version: Literal["M2-00-city-max600-ceil20pct-minute-v1"] = BUFFER_VERSION


class F03ValidationReport(PlaceContract):
    schema_version: Literal["f03-validation-report-v1"] = "f03-validation-report-v1"
    rule_version: Literal["F03-synthetic-v1"] = RULE_VERSION
    evaluated_at: datetime
    input_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    environment: Literal["synthetic"] = "synthetic"
    evaluated_checks: tuple[Literal["F03"]] = ("F03",)
    f04_status: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"
    checks: tuple[F03FeasibilityCheck, ...]
    status: Status
    valid_until: datetime | None
    overall_trip_ready: Literal[False] = False
    usable_for_real_trip: Literal[False] = False
    usable_in_confirmed_snapshot: Literal[False] = False

    @model_validator(mode="after")
    def coherent(self):
        if self.evaluated_at.utcoffset() is None or self.status != aggregate_status(tuple(c.status for c in self.checks)):
            raise ValueError("aware clock and correct aggregation required")
        if self.valid_until is not None and (self.status != "PASS" or self.valid_until.utcoffset() is None or self.valid_until <= self.evaluated_at):
            raise ValueError("invalid report lifetime")
        return self


def check_pair(pair: ConsecutiveActivityPair, facts: tuple[TravelLegFacts, ...],
               registry: tuple[LegEndpoint, ...], now: datetime) -> tuple[F03FeasibilityCheck, ...]:
    checks = []

    def add(status, reason, used=(), total=None, buffer=None, required=None):
        checks.append(F03FeasibilityCheck(
            pair_id=pair.pair_id, leg_id=pair.leg_id,
            item_ids=(pair.previous.item_id, pair.next.item_id),
            from_exit=pair.from_exit, to_entrance=pair.to_entrance,
            target_departure=pair.previous.end, target_next_start=pair.next.start,
            status=status, reason_code=reason, fact_ids=tuple(sorted(f.fact_id for f in used)),
            duration_seconds=total, extra_buffer_seconds=buffer, required_next_start=required,
            severity="info" if status == "PASS" else "blocker",
            action="none" if status == "PASS" else "revise_activities" if status == "FAIL" else "obtain_facts"))

    times = (pair.previous.start, pair.previous.end, pair.next.start, pair.next.end)
    if any(t.utcoffset() != timedelta(hours=8) for t in times) or any(a.end <= a.start for a in (pair.previous, pair.next)):
        add("FAIL", "invalid_time")
        return tuple(checks)
    if pair.next.start < pair.previous.end:
        add("FAIL", "activity_overlap")
    if len({t.date() for t in times}) != 1:
        add("UNKNOWN", "cross_day_unsupported")
        return tuple(checks)
    if (pair.from_exit.role != "exit" or pair.to_entrance.role != "entrance"
        or pair.from_exit.resource != pair.previous.resource or pair.to_entrance.resource != pair.next.resource
        or pair.from_exit not in registry or pair.to_entrance not in registry):
        add("FAIL", "endpoint_binding_mismatch")
        return tuple(checks)
    if pair.policy_scope != "same_city" or pair.previous.resource.city != pair.next.resource.city:
        add("UNKNOWN", "policy_scope_unsupported")
        return tuple(checks)
    selected = tuple(f for f in facts if f.leg_id == pair.leg_id and f.from_exit == pair.from_exit and f.to_entrance == pair.to_entrance)
    if not selected:
        add("UNKNOWN", "missing_matching_leg")
        return tuple(checks)
    for fact in selected:
        scope = fact.applicable_scope
        total = fact.duration.total() if fact.duration else None
        reason = None
        if not scope.start_date <= pair.previous.end.date() <= scope.end_date:
            reason = "date_not_applicable"
        elif now < fact.source.observed_at:
            reason = "observation_in_future"
        elif now >= fact.source.expires_at:
            reason = "source_expired"
        elif total is None:
            reason = "duration_incomplete"
        elif not (scope.window_start <= pair.previous.end.time() < scope.window_end
                  and pair.previous.end + timedelta(seconds=total) <= datetime.combine(pair.previous.end.date(), scope.window_end, pair.previous.end.tzinfo)):
            reason = "time_window_not_applicable"
        if reason:
            add("UNKNOWN", reason, (fact,))
    # Unresolved candidate facts cannot be bypassed by picking another source.
    if any(c.status == "UNKNOWN" for c in checks):
        return tuple(checks)
    if any(f.duration != selected[0].duration for f in selected[1:]):
        add("UNKNOWN", "source_conflict", selected)
        return tuple(checks)
    total = selected[0].duration.total()
    buffer = max(600, ((total + 299) // 300) * 60)
    required = pair.previous.end + timedelta(seconds=total + buffer)
    if required.date() != pair.previous.end.date():
        add("UNKNOWN", "cross_day_unsupported", selected)
    else:
        passed = pair.next.start >= required
        add("PASS" if passed else "FAIL", "time_sufficient" if passed else "time_insufficient", selected, total, buffer, required)
    return tuple(checks)
