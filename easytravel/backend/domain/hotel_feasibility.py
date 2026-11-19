"""F05 synthetic hotel policy arithmetic; no inventory, price or booking authority."""
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .places import ApplicableScope, Identifier, PlaceContract, ResourceRef, Text
from .opening_feasibility import LocalMinute, OpeningSource, Status, aggregate_status

RULE_VERSION = "F05-synthetic-v1"


class HotelSource(OpeningSource):
    provider: Literal["synthetic_hotel_fixture"] = "synthetic_hotel_fixture"
    source_reference: Annotated[str, Field(pattern=r"^synthetic-hotel:[A-Za-z0-9_-]+$", max_length=100)]


class LuggageStoragePolicy(PlaceContract):
    state: Literal["ALLOWED", "NOT_ALLOWED", "UNKNOWN"]
    start: LocalMinute | None
    end: LocalMinute | None

    @model_validator(mode="after")
    def window(self):
        if self.state == "ALLOWED":
            if self.start is None or self.end is None or self.start >= self.end:
                raise ValueError("explicit positive same-day luggage window required")
        elif self.start is not None or self.end is not None:
            raise ValueError("unknown/denied luggage has no allowed window")
        return self


class CheckInPolicy(PlaceContract):
    earliest: LocalMinute | None
    latest: LocalMinute | None
    latest_day_offset: Literal[0, 1]
    early_from: LocalMinute | None  # Explicit synthetic permission, never a real confirmation.
    luggage: LuggageStoragePolicy | None

    @field_validator("latest_day_offset", mode="before")
    @classmethod
    def integer_offset(cls, value):
        if type(value) is not int:
            raise ValueError("integer day offset required")
        return value

    @model_validator(mode="after")
    def ordered(self):
        if self.early_from is not None and (self.earliest is None or self.early_from >= self.earliest):
            raise ValueError("early permission must precede known normal earliest time")
        if self.latest_day_offset == 0 and self.earliest is not None and self.latest is not None and self.latest < self.earliest:
            raise ValueError("same-day latest cannot precede earliest")
        return self


class CheckOutPolicy(PlaceContract):
    deadline: LocalMinute | None
    late_until: LocalMinute | None

    @model_validator(mode="after")
    def ordered(self):
        if self.late_until is not None and (self.deadline is None or self.late_until <= self.deadline):
            raise ValueError("late permission must follow known normal deadline")
        return self


class RoomCapacity(PlaceContract):
    room_type_id: Identifier
    max_guests_per_room: Annotated[int, Field(ge=1, le=20)] | None


class HotelPolicyFacts(PlaceContract):
    schema_version: Literal["hotel-policy-facts-v1"] = "hotel-policy-facts-v1"
    fact_id: Identifier
    resource: ResourceRef
    room_type_id: Identifier
    applicable_scope: ApplicableScope
    source: HotelSource
    date_policies_complete: bool  # Relevant date exceptions known; not source verification.
    check_in: CheckInPolicy | None
    check_out: CheckOutPolicy | None
    capacity: RoomCapacity | None
    note: Text | None = None  # Inert external text.

    @model_validator(mode="after")
    def room_binding(self):
        if self.capacity is not None and self.capacity.room_type_id != self.room_type_id:
            raise ValueError("capacity room type binding mismatch")
        return self


class HotelStay(PlaceContract):
    stay_id: Identifier
    resource: ResourceRef
    room_type_id: Identifier
    room_count: Annotated[int, Field(ge=1, le=20)]
    check_in_date: date  # First booked night, even if permitted check-in occurs after midnight.
    check_out_date: date  # Exclusive end of booked nights.
    arrival_at: datetime
    check_in_at: datetime
    check_out_at: datetime
    pre_check_in_plan: Literal["none", "luggage_storage", "offsite_wait"]


class F05ValidationInput(PlaceContract):
    trip_start_date: date
    trip_end_date: date  # Inclusive travel date; required nights are [start, end).
    party_size: Annotated[int, Field(ge=1, le=100)]
    resolved_hotels: Annotated[tuple[ResourceRef, ...], Field(max_length=30)]
    stays: Annotated[tuple[HotelStay, ...], Field(max_length=30)]
    facts: Annotated[tuple[HotelPolicyFacts, ...], Field(max_length=100)]

    @model_validator(mode="after")
    def bounded_unique(self):
        if not 0 <= (self.trip_end_date - self.trip_start_date).days <= 30:
            raise ValueError("ordered travel dates, at most 31 travel days required")
        for keys in ([s.stay_id for s in self.stays], [f.fact_id for f in self.facts],
                     [r.internal_resource_id for r in self.resolved_hotels]):
            if len(keys) != len(set(keys)):
                raise ValueError("duplicate identity")
        return self


class StayNightCoverage(PlaceContract):
    night_date: date
    required: bool
    stay_ids: tuple[Identifier, ...]
    status: Literal["PASS", "FAIL"]

    @model_validator(mode="after")
    def counted_once(self):
        if len(set(self.stay_ids)) != len(self.stay_ids):
            raise ValueError("a stay counts at most once per night")
        expected = "PASS" if self.required and len(self.stay_ids) == 1 else "FAIL"
        if self.status != expected:
            raise ValueError("incorrect night coverage status")
        return self


Reason = Literal["zero_night_trip", "night_covered", "night_missing", "night_duplicate", "night_outside_trip",
    "invalid_timezone", "invalid_event_order", "stay_dates_invalid", "stay_outside_trip", "event_date_mismatch",
    "resource_binding_mismatch", "missing_policy", "source_expired", "observation_in_future", "date_not_applicable",
    "date_policies_incomplete", "source_conflict", "check_in_unknown", "check_in_earliest_unknown",
    "check_in_latest_unknown", "before_check_in", "after_check_in_window", "check_in_allowed",
    "check_out_unknown", "after_check_out", "check_out_allowed", "capacity_unknown", "capacity_exceeded",
    "capacity_sufficient", "luggage_unknown", "luggage_denied", "luggage_outside_window", "luggage_allowed",
    "luggage_cross_day_unsupported", "early_arrival_plan_unknown", "offsite_wait_explicit", "no_pre_check_in_service"]


class F05FeasibilityCheck(PlaceContract):
    check_id: Literal["F05.structure", "F05.check_in", "F05.check_out", "F05.arrival", "F05.capacity", "F05.nights"]
    stay_ids: tuple[Identifier, ...]
    resource: ResourceRef | None
    room_type_id: Identifier | None
    target_start_date: date
    target_end_date: date
    status: Status
    reason_code: Reason
    fact_ids: tuple[Identifier, ...]
    severity: Literal["info", "blocker"]
    action: Literal["none", "revise_stay", "obtain_facts"]
    event_at: datetime | None = None
    allowed_from: datetime | None = None
    allowed_until: datetime | None = None
    total_capacity: Annotated[int, Field(ge=1, le=400)] | None = None
    rule_version: Literal["F05-synthetic-v1"] = RULE_VERSION


class F05ValidationReport(PlaceContract):
    schema_version: Literal["f05-validation-report-v1"] = "f05-validation-report-v1"
    rule_version: Literal["F05-synthetic-v1"] = RULE_VERSION
    evaluated_at: datetime
    input_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    environment: Literal["synthetic"] = "synthetic"
    evaluated_checks: tuple[Literal["F05"]] = ("F05",)
    other_checks_status: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"
    inventory_status: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"
    checks: tuple[F05FeasibilityCheck, ...]
    night_coverage: tuple[StayNightCoverage, ...]
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


def check_stay(s: HotelStay, request: F05ValidationInput, now: datetime) -> tuple[F05FeasibilityCheck, ...]:
    checks = []

    def add(kind, status, reason, facts=(), event=None, start=None, end=None, capacity=None):
        checks.append(F05FeasibilityCheck(check_id="F05." + kind, stay_ids=(s.stay_id,), resource=s.resource,
            room_type_id=s.room_type_id, target_start_date=s.check_in_date, target_end_date=s.check_out_date,
            status=status, reason_code=reason, fact_ids=tuple(sorted(f.fact_id for f in facts)),
            severity="info" if status in ("PASS", "NOT_APPLICABLE") else "blocker",
            action="revise_stay" if status == "FAIL" else "obtain_facts" if status == "UNKNOWN" else "none",
            event_at=event, allowed_from=start, allowed_until=end, total_capacity=capacity))

    times = (s.arrival_at, s.check_in_at, s.check_out_at)
    if any(t.utcoffset() != timedelta(hours=8) for t in times):
        add("structure", "FAIL", "invalid_timezone")
        return tuple(checks)
    if not s.arrival_at <= s.check_in_at < s.check_out_at:
        add("structure", "FAIL", "invalid_event_order")
        return tuple(checks)
    if not 1 <= (s.check_out_date - s.check_in_date).days <= 30:
        add("structure", "FAIL", "stay_dates_invalid")
        return tuple(checks)
    if not request.trip_start_date <= s.check_in_date < s.check_out_date <= request.trip_end_date:
        add("structure", "FAIL", "stay_outside_trip")
    if (s.arrival_at.date() not in (s.check_in_date, s.check_in_date + timedelta(days=1))
        or s.check_in_at.date() not in (s.check_in_date, s.check_in_date + timedelta(days=1))
        or s.check_out_at.date() != s.check_out_date):
        add("structure", "FAIL", "event_date_mismatch")
        return tuple(checks)
    if s.resource not in request.resolved_hotels:
        add("structure", "FAIL", "resource_binding_mismatch")
        return tuple(checks)
    selected = tuple(f for f in request.facts if f.resource == s.resource and f.room_type_id == s.room_type_id)
    if not selected:
        add("check_in", "UNKNOWN", "missing_policy")
        return tuple(checks)
    for f in selected:
        reason = None
        if not f.applicable_scope.start_date <= s.check_in_date < s.check_out_date <= f.applicable_scope.end_date:
            reason = "date_not_applicable"
        elif now < f.source.observed_at:
            reason = "observation_in_future"
        elif now >= f.source.expires_at:
            reason = "source_expired"
        elif not f.date_policies_complete:
            reason = "date_policies_incomplete"
        if reason: add("check_in", "UNKNOWN", reason, (f,))
    if any(c.status == "UNKNOWN" for c in checks):
        return tuple(checks)  # No other source can bypass incomplete/expired date policy.

    def dimension(name, kind, unknown):
        values = tuple(getattr(f, name) for f in selected)
        if any(v != values[0] for v in values[1:]):
            add(kind, "UNKNOWN", "source_conflict", selected)
            return None
        if values[0] is None:
            add(kind, "UNKNOWN", unknown, selected)
        return values[0]

    policy = dimension("check_in", "check_in", "check_in_unknown")
    if policy is not None:
        lower = policy.early_from or policy.earliest
        earliest = datetime.combine(s.check_in_date, lower, s.check_in_at.tzinfo) if lower else None
        latest = datetime.combine(s.check_in_date + timedelta(days=policy.latest_day_offset), policy.latest, s.check_in_at.tzinfo) if policy.latest else None
        if earliest is None: add("check_in", "UNKNOWN", "check_in_earliest_unknown", selected)
        elif s.check_in_at < earliest: add("check_in", "FAIL", "before_check_in", selected, s.check_in_at, earliest, latest)
        if latest is None: add("check_in", "UNKNOWN", "check_in_latest_unknown", selected)
        elif s.check_in_at > latest: add("check_in", "FAIL", "after_check_in_window", selected, s.check_in_at, earliest, latest)
        if earliest is not None and latest is not None and earliest <= s.check_in_at <= latest:
            add("check_in", "PASS", "check_in_allowed", selected, s.check_in_at, earliest, latest)
        if s.arrival_at == s.check_in_at:
            add("arrival", "NOT_APPLICABLE", "no_pre_check_in_service")
        elif s.pre_check_in_plan == "offsite_wait":
            add("arrival", "NOT_APPLICABLE", "offsite_wait_explicit")
        elif s.pre_check_in_plan == "luggage_storage":
            luggage = policy.luggage
            if luggage is None or luggage.state == "UNKNOWN":
                add("arrival", "UNKNOWN", "luggage_unknown", selected)
            elif luggage.state == "NOT_ALLOWED":
                add("arrival", "FAIL", "luggage_denied", selected)
            elif s.arrival_at.date() != s.check_in_at.date():
                add("arrival", "UNKNOWN", "luggage_cross_day_unsupported", selected)
            else:
                a = datetime.combine(s.arrival_at.date(), luggage.start, s.arrival_at.tzinfo)
                b = datetime.combine(s.arrival_at.date(), luggage.end, s.arrival_at.tzinfo)
                permitted = a <= s.arrival_at and s.check_in_at <= b
                add("arrival", "PASS" if permitted else "FAIL", "luggage_allowed" if permitted else "luggage_outside_window", selected, s.arrival_at, a, b)
        elif earliest is None or s.arrival_at < earliest:
            add("arrival", "UNKNOWN", "early_arrival_plan_unknown", selected)
        else:
            add("arrival", "PASS", "no_pre_check_in_service", selected)

    out = dimension("check_out", "check_out", "check_out_unknown")
    if out is not None:
        deadline = out.late_until or out.deadline
        if deadline is None: add("check_out", "UNKNOWN", "check_out_unknown", selected)
        else:
            end = datetime.combine(s.check_out_date, deadline, s.check_out_at.tzinfo)
            passed = s.check_out_at <= end
            add("check_out", "PASS" if passed else "FAIL", "check_out_allowed" if passed else "after_check_out", selected, s.check_out_at, end=end)
    room = dimension("capacity", "capacity", "capacity_unknown")
    if room is not None:
        if room.max_guests_per_room is None: add("capacity", "UNKNOWN", "capacity_unknown", selected)
        else:
            capacity = s.room_count * room.max_guests_per_room
            passed = request.party_size <= capacity
            add("capacity", "PASS" if passed else "FAIL", "capacity_sufficient" if passed else "capacity_exceeded", selected, capacity=capacity)
    return tuple(checks)


def check_nights(request: F05ValidationInput) -> tuple[tuple[StayNightCoverage, ...], tuple[F05FeasibilityCheck, ...]]:
    required = {request.trip_start_date + timedelta(days=i) for i in range((request.trip_end_date - request.trip_start_date).days)}
    counts = {day: [] for day in required}
    for stay in request.stays:
        span = (stay.check_out_date - stay.check_in_date).days
        if 1 <= span <= 30:
            for i in range(span): counts.setdefault(stay.check_in_date + timedelta(days=i), []).append(stay.stay_id)
    coverage, checks = [], []
    for day, ids in sorted(counts.items()):
        needed = day in required
        status = "PASS" if needed and len(ids) == 1 else "FAIL"
        reason = "night_outside_trip" if not needed else "night_missing" if not ids else "night_duplicate" if len(ids) > 1 else "night_covered"
        coverage.append(StayNightCoverage(night_date=day, required=needed, stay_ids=tuple(sorted(ids)), status=status))
        checks.append(F05FeasibilityCheck(check_id="F05.nights", stay_ids=tuple(sorted(ids)), resource=None, room_type_id=None,
            target_start_date=day, target_end_date=day + timedelta(days=1), status=status, reason_code=reason, fact_ids=(),
            severity="info" if status == "PASS" else "blocker", action="none" if status == "PASS" else "revise_stay"))
    if not counts and not request.stays:
        checks.append(F05FeasibilityCheck(check_id="F05.nights", stay_ids=(), resource=None, room_type_id=None,
            target_start_date=request.trip_start_date, target_end_date=request.trip_end_date, status="NOT_APPLICABLE",
            reason_code="zero_night_trip", fact_ids=(), severity="info", action="none"))
    return tuple(coverage), tuple(checks)
