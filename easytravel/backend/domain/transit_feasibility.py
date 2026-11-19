"""F04: explicit synthetic occurrences, not a journey planner or live service claim."""
from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .places import Identifier, PlaceContract, ResourceRef, Text
from .opening_feasibility import OpeningSource, Status, aggregate_status

RULE_VERSION = "F04-synthetic-v1"
POLICY_VERSION = "F04-explicit-walk-plus-buffer-seconds-v1"
Seconds = Annotated[int, Field(ge=0, le=86400)]


class TransitStopRef(PlaceContract):
    resource: ResourceRef  # internal_resource_id is independent of provider IDs
    access_id: Identifier
    role: Literal["entrance", "exit"]


class TransitSegment(PlaceContract):
    segment_id: Identifier
    internal_line_id: Identifier
    provider: Identifier
    provider_line_id: Identifier
    direction_id: Identifier
    boarding_stop: TransitStopRef
    alighting_stop: TransitStopRef

    @model_validator(mode="after")
    def roles(self):
        if self.boarding_stop.role != "entrance" or self.alighting_stop.role != "exit":
            raise ValueError("explicit boarding entrance and alighting exit required")
        return self


class TransitSource(OpeningSource):
    provider: Literal["synthetic_transit_fixture"] = "synthetic_transit_fixture"
    source_reference: Annotated[str, Field(pattern=r"^synthetic-transit:[A-Za-z0-9_-]+$", max_length=100)]


class TransitFactScope(PlaceContract):
    service_date: date
    starts_at: datetime
    ends_at: datetime

    @model_validator(mode="after")
    def ordered(self):
        if any(t.utcoffset() != timedelta(hours=8) for t in (self.starts_at, self.ends_at)) or self.starts_at >= self.ends_at:
            raise ValueError("ordered +08:00 scope required")
        return self


class ServiceOccurrence(PlaceContract):
    fact_id: Identifier
    occurrence_id: Identifier
    segment: TransitSegment
    scope: TransitFactScope
    source: TransitSource
    state: Literal["OPERATING", "NO_SERVICE", "UNKNOWN"]
    date_information_complete: bool
    suspension: Literal["CLEAR", "SUSPENDED", "UNKNOWN"]
    # These events are AT THE SELECTED BOARDING/ALIGHTING STOPS.
    origin_departure: datetime | None = None  # Never substitutes for board_departure.
    board_arrival: datetime | None
    board_departure: datetime | None
    alight_arrival: datetime | None
    first_board_departure: datetime | None
    last_board_departure: datetime | None
    note: Text | None = None  # Inert external text.

    @model_validator(mode="after")
    def no_service_has_no_trip(self):
        if self.state != "OPERATING" and any(t is not None for t in (
            self.origin_departure, self.board_arrival, self.board_departure, self.alight_arrival,
            self.first_board_departure, self.last_board_departure)):
            raise ValueError("non-operating observation cannot assert trip events")
        return self


class TimeAllowance(PlaceContract):
    walking_seconds: Seconds | None
    buffer_seconds: Seconds | None
    required_seconds: Seconds | None
    buffer_included_in_required: Literal[True] = True
    policy_version: Literal["F04-explicit-walk-plus-buffer-seconds-v1"] = POLICY_VERSION

    @model_validator(mode="after")
    def sum_once(self):
        if self.required_seconds is not None and (self.walking_seconds is None or self.buffer_seconds is None
            or self.required_seconds != self.walking_seconds + self.buffer_seconds):
            raise ValueError("required seconds must include walking and buffer exactly once")
        return self


class FirstBoardingAccess(PlaceContract):
    fact_id: Identifier
    rule_id: Identifier
    activity_resource: ResourceRef
    activity_exit_id: Identifier
    to_stop: TransitStopRef
    scope: TransitFactScope
    source: TransitSource
    allowance: TimeAllowance


class TransferRule(PlaceContract):
    fact_id: Identifier
    rule_id: Identifier
    from_stop: TransitStopRef
    to_stop: TransitStopRef
    scope: TransitFactScope
    source: TransitSource
    allowance: TimeAllowance

    @model_validator(mode="after")
    def directed(self):
        if self.from_stop.role != "exit" or self.to_stop.role != "entrance":
            raise ValueError("transfer must connect exact exit to entrance")
        return self


RiskFlag = Literal["near_last_service", "remote_only", "mandatory", "hard_constraint", "intercity", "airport"]


class TransitConnection(PlaceContract):
    connection_id: Identifier
    activity_resource: ResourceRef
    activity_exit_id: Identifier
    previous_end: datetime
    segments: Annotated[tuple[TransitSegment, ...], Field(min_length=1, max_length=2)]
    occurrence_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=2)]
    first_access_rule_id: Identifier
    transfer_rule_id: Identifier | None
    risk_flags: Annotated[tuple[RiskFlag, ...], Field(max_length=6)] = ()
    requested_scope: Literal["same_city_same_day", "cross_midnight", "rail", "airport", "real_time"] = "same_city_same_day"

    @model_validator(mode="after")
    def sequence(self):
        if len(self.segments) != len(self.occurrence_ids) or len(set(s.segment_id for s in self.segments)) != len(self.segments):
            raise ValueError("one explicit occurrence per distinct segment required")
        if len(self.segments) == 1 and self.transfer_rule_id is not None:
            raise ValueError("single ride has no transfer")
        if len(set(self.risk_flags)) != len(self.risk_flags):
            raise ValueError("duplicate risk flags")
        return self


class F04ValidationInput(PlaceContract):
    # Internal DTO; resolved stops and risk flags come from trusted server policy.
    resolved_stops: Annotated[tuple[TransitStopRef, ...], Field(max_length=200)]
    connections: Annotated[tuple[TransitConnection, ...], Field(max_length=100)]
    services: Annotated[tuple[ServiceOccurrence, ...], Field(max_length=400)]
    first_access_rules: Annotated[tuple[FirstBoardingAccess, ...], Field(max_length=200)]
    transfer_rules: Annotated[tuple[TransferRule, ...], Field(max_length=200)]

    @model_validator(mode="after")
    def identities(self):
        for keys in ([c.connection_id for c in self.connections],
                     [f.fact_id for f in (*self.services, *self.first_access_rules, *self.transfer_rules)],
                     [(s.resource.internal_resource_id, s.access_id, s.role) for s in self.resolved_stops]):
            if len(keys) != len(set(keys)):
                raise ValueError("duplicate identity")
        refs = {}
        for stop in self.resolved_stops:
            key = stop.resource.internal_resource_id
            if key in refs and refs[key] != stop.resource:
                raise ValueError("conflicting internal stop binding")
            refs[key] = stop.resource
        return self


Reason = Literal["unsupported_scope", "invalid_request_time", "stop_binding_mismatch",
    "missing_service", "date_not_applicable", "cross_day_unsupported", "source_expired",
    "observation_in_future", "source_conflict", "date_information_incomplete", "service_unknown",
    "suspension_unknown", "no_service", "suspended", "missing_stop_events", "invalid_service_events",
    "first_service_unknown", "last_service_unknown", "operating_window_conflict", "service_events_known",
    "missing_access_rule", "missing_transfer_rule", "allowance_unknown", "fact_window_not_applicable",
    "first_boarding_catchable", "first_boarding_missed", "transfer_sufficient", "transfer_missed"]


class F04FeasibilityCheck(PlaceContract):
    check_id: Literal["F04.service", "F04.first_boarding", "F04.transfer", "F04.scope"]
    connection_id: Identifier
    segment_ids: tuple[Identifier, ...]
    occurrence_ids: tuple[Identifier, ...]
    stops: tuple[TransitStopRef, ...]
    target_date: date
    status: Status
    reason_code: Reason
    fact_ids: tuple[Identifier, ...]
    risk_scope: Literal["ordinary", "critical"]
    severity: Literal["info", "warning", "blocker"]
    action: Literal["none", "revise_connection", "obtain_facts"]
    ready_at: datetime | None = None
    vehicle_arrival_at: datetime | None = None
    boarding_at: datetime | None = None
    departure_at: datetime | None = None
    alight_arrival_at: datetime | None = None
    required_seconds: Seconds | None = None
    rule_version: Literal["F04-synthetic-v1"] = RULE_VERSION
    policy_version: Literal["F04-explicit-walk-plus-buffer-seconds-v1"] = POLICY_VERSION


class F04ValidationReport(PlaceContract):
    schema_version: Literal["f04-validation-report-v1"] = "f04-validation-report-v1"
    rule_version: Literal["F04-synthetic-v1"] = RULE_VERSION
    evaluated_at: datetime
    input_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    environment: Literal["synthetic"] = "synthetic"
    evaluated_checks: tuple[Literal["F04"]] = ("F04",)
    f03_status: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"
    checks: tuple[F04FeasibilityCheck, ...]
    status: Status
    valid_until: datetime | None
    overall_trip_ready: Literal[False] = False
    usable_for_real_trip: Literal[False] = False
    usable_in_confirmed_snapshot: Literal[False] = False

    @model_validator(mode="after")
    def consistent(self):
        if self.evaluated_at.utcoffset() is None or self.status != aggregate_status(tuple(c.status for c in self.checks)):
            raise ValueError("aware clock and correct aggregate required")
        if self.valid_until is not None and (self.status != "PASS" or self.valid_until.utcoffset() is None or self.valid_until <= self.evaluated_at):
            raise ValueError("invalid report lifetime")
        return self


def fact_problem(fact, target: date, now: datetime):
    if fact.scope.service_date != target:
        return "date_not_applicable"
    if any(t.date() != target for t in (fact.scope.starts_at, fact.scope.ends_at)):
        return "cross_day_unsupported"
    if now < fact.source.observed_at:
        return "observation_in_future"
    if now >= fact.source.expires_at:
        return "source_expired"
    return None


def check_connection(c: TransitConnection, request: F04ValidationInput, now: datetime) -> tuple[F04FeasibilityCheck, ...]:
    checks = []
    target = c.previous_end.date()
    # Missing/contradictory facts may raise severity, never lower server risk flags.
    near_last = any(f.last_board_departure is not None and f.board_departure is not None
        and f.last_board_departure.utcoffset() is not None and f.board_departure.utcoffset() is not None
        and timedelta(0) <= f.last_board_departure - f.board_departure <= timedelta(minutes=30)
        for f in request.services if f.occurrence_id in c.occurrence_ids)
    critical = bool(c.risk_flags) or near_last

    def add(check, status, reason, facts=(), ready=None, departure=None, seconds=None, service=None):
        checks.append(F04FeasibilityCheck(
            check_id="F04." + check, connection_id=c.connection_id,
            segment_ids=tuple(s.segment_id for s in c.segments), occurrence_ids=c.occurrence_ids,
            stops=tuple(s for leg in c.segments for s in (leg.boarding_stop, leg.alighting_stop)),
            target_date=target, status=status, reason_code=reason, fact_ids=tuple(sorted(f.fact_id for f in facts)),
            risk_scope="critical" if critical else "ordinary",
            severity="blocker" if status == "FAIL" or (status == "UNKNOWN" and critical) else "warning" if status == "UNKNOWN" else "info",
            action="none" if status == "PASS" else "revise_connection" if status == "FAIL" else "obtain_facts",
            ready_at=ready, departure_at=departure, required_seconds=seconds,
            vehicle_arrival_at=service.board_arrival if service else None,
            boarding_at=max(ready, service.board_arrival) if service and status == "PASS" else None,
            alight_arrival_at=service.alight_arrival if service else None))

    if c.previous_end.utcoffset() != timedelta(hours=8):
        add("scope", "FAIL", "invalid_request_time")
        return tuple(checks)
    stops = tuple(s for leg in c.segments for s in (leg.boarding_stop, leg.alighting_stop))
    if any(s not in request.resolved_stops for s in stops):
        add("scope", "FAIL", "stop_binding_mismatch")
        return tuple(checks)
    if (c.requested_scope != "same_city_same_day" or {s.resource.city for s in stops} != {c.activity_resource.city}
        or any(f in c.risk_flags for f in ("intercity", "airport"))):
        add("scope", "UNKNOWN", "unsupported_scope")
        return tuple(checks)

    def resolve(facts, check):
        if not facts:
            return None
        problems = [(f, fact_problem(f, target, now)) for f in facts]
        if any(reason for _, reason in problems):
            for f, reason in problems:
                if reason: add(check, "UNKNOWN", reason, (f,))
            return None
        # Compare substantive assertions, not metadata or inert prose. Never choose best source.
        assertions = [f.model_dump(exclude={"fact_id", "source", "note"}) for f in facts]
        if any(a != assertions[0] for a in assertions[1:]):
            add(check, "UNKNOWN", "source_conflict", facts)
            return None
        return facts[0]

    services = []
    used_services = []
    for segment, occurrence_id in zip(c.segments, c.occurrence_ids):
        selected = tuple(f for f in request.services if f.occurrence_id == occurrence_id and f.segment == segment)
        used_services.append(selected)
        f = resolve(selected, "service")
        services.append(None)
        if not selected:
            add("service", "UNKNOWN", "missing_service")
        if f is None:
            continue
        if not f.date_information_complete:
            add("service", "UNKNOWN", "date_information_incomplete", selected)
            continue
        if f.state == "NO_SERVICE" or f.suspension == "SUSPENDED":
            # No trip events exist to localize a negative claim. The minimal
            # contract only accepts full-day negative service coverage.
            if not (f.scope.starts_at.time() == time.min and f.scope.ends_at.time() == time.max):
                add("service", "UNKNOWN", "fact_window_not_applicable", selected)
                continue
            add("service", "FAIL", "no_service" if f.state == "NO_SERVICE" else "suspended", selected)
            continue
        if f.state == "UNKNOWN" or f.suspension == "UNKNOWN":
            add("service", "UNKNOWN", "service_unknown" if f.state == "UNKNOWN" else "suspension_unknown", selected)
            continue
        events = (f.board_arrival, f.board_departure, f.alight_arrival)
        if any(t is None for t in events):
            add("service", "UNKNOWN", "missing_stop_events", selected)
            continue
        all_times = tuple(t for t in (*events, f.origin_departure, f.first_board_departure, f.last_board_departure) if t is not None)
        if any(t.utcoffset() != timedelta(hours=8) for t in all_times):
            add("service", "UNKNOWN", "invalid_service_events", selected)
            continue
        if any(t.date() != target for t in all_times):
            add("service", "UNKNOWN", "cross_day_unsupported", selected)
            continue
        if not (f.board_arrival <= f.board_departure < f.alight_arrival) or (f.origin_departure is not None and f.origin_departure > f.board_arrival):
            add("service", "UNKNOWN", "invalid_service_events", selected)
            continue
        if not (f.scope.starts_at <= f.board_arrival and f.alight_arrival <= f.scope.ends_at):
            add("service", "UNKNOWN", "fact_window_not_applicable", selected)
            continue
        if f.first_board_departure is not None and f.last_board_departure is not None and f.first_board_departure > f.last_board_departure:
            add("service", "UNKNOWN", "invalid_service_events", selected)
            continue
        # Complete event facts remain independently usable even if operating boundaries are missing.
        services[-1] = f
        if f.first_board_departure is None: add("service", "UNKNOWN", "first_service_unknown", selected)
        if f.last_board_departure is None: add("service", "UNKNOWN", "last_service_unknown", selected)
        if ((f.first_board_departure is not None and f.board_departure < f.first_board_departure)
            or (f.last_board_departure is not None and f.board_departure > f.last_board_departure)):
            add("service", "FAIL", "operating_window_conflict", selected)
        elif f.first_board_departure is not None and f.last_board_departure is not None:
            add("service", "PASS", "service_events_known", selected)

    first_rules = tuple(f for f in request.first_access_rules if f.rule_id == c.first_access_rule_id
        and f.activity_resource == c.activity_resource and f.activity_exit_id == c.activity_exit_id
        and f.to_stop == c.segments[0].boarding_stop)
    first = resolve(first_rules, "first_boarding")
    if not first_rules: add("first_boarding", "UNKNOWN", "missing_access_rule")

    def timing(rule, selected, start, service, dependencies, check):
        # Non-negative access/transfer times cannot rescue an already missed
        # departure. This lower-bound failure is independent of missing policy.
        already_missed = service is not None and start > service.board_departure
        if already_missed:
            add(check, "FAIL", "first_boarding_missed" if check == "first_boarding" else "transfer_missed",
                dependencies, start, service.board_departure)
        if rule is None:
            return
        seconds = rule.allowance.required_seconds
        if seconds is None:
            add(check, "UNKNOWN", "allowance_unknown", selected)
            return
        ready = start + timedelta(seconds=seconds)
        if ready.date() != target:
            add(check, "UNKNOWN", "cross_day_unsupported", selected)
            return
        if not (rule.scope.starts_at <= start and ready <= rule.scope.ends_at):
            add(check, "UNKNOWN", "fact_window_not_applicable", selected)
            return
        if service is None:
            return  # Its independent UNKNOWN/FAIL is already recorded; never invent a departure.
        if already_missed:
            return
        passed = ready <= service.board_departure
        reason = ("first_boarding_catchable" if passed else "first_boarding_missed") if check == "first_boarding" else ("transfer_sufficient" if passed else "transfer_missed")
        add(check, "PASS" if passed else "FAIL", reason, (*selected, *dependencies), ready, service.board_departure, seconds, service)

    timing(first, first_rules, c.previous_end, services[0], used_services[0], "first_boarding")
    if len(c.segments) == 2:
        rules = tuple(f for f in request.transfer_rules if f.rule_id == c.transfer_rule_id
            and f.from_stop == c.segments[0].alighting_stop and f.to_stop == c.segments[1].boarding_stop)
        rule = resolve(rules, "transfer")
        if not rules: add("transfer", "UNKNOWN", "missing_transfer_rule")
        if services[0] is not None:
            timing(rule, rules, services[0].alight_arrival, services[1], (*used_services[0], *used_services[1]), "transfer")
    return tuple(checks)
