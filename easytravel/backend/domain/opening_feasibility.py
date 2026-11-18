"""F02 only: deterministic arithmetic over fabricated, explicitly scoped facts.

No source verification, coordinates, itinerary readiness or confirmation authority.
"""
from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from .places import ApplicableScope, Identifier, PlaceContract, ResourceRef, Text


def local_minute(value: time) -> time:
    if value.tzinfo is not None or value.second or value.microsecond:
        raise ValueError("local wall-clock minute required")
    return value


LocalMinute = Annotated[time, AfterValidator(local_minute)]
Status = Literal["PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"]
RULE_VERSION = "F02-synthetic-v1"


class OpeningWindow(PlaceContract):
    start: LocalMinute
    end: LocalMinute

    @model_validator(mode="after")
    def ordered(self):
        if self.start >= self.end:
            raise ValueError("same-day positive opening window required")
        return self


class AdmissionRule(PlaceContract):
    # None explicitly means no separate admission cutoff; absent rule is UNKNOWN.
    last_entry: LocalMinute | None
    minimum_visit_minutes: Annotated[int, Field(ge=1, le=1440)]
    reservation_required: bool
    # Unimplemented textual restrictions can only make a result UNKNOWN.
    other_restrictions: Annotated[tuple[Text, ...], Field(max_length=10)] = ()


class DayOpening(PlaceContract):
    closed: bool
    windows: Annotated[tuple[OpeningWindow, ...], Field(max_length=8)]
    admission: AdmissionRule | None

    @model_validator(mode="after")
    def consistent(self):
        if self.closed:
            if self.windows or self.admission is not None:
                raise ValueError("closed day cannot declare open windows or admission")
        elif not self.windows:
            raise ValueError("open day requires explicit windows")
        if any(a.end > b.start for a, b in zip(self.windows, self.windows[1:])):
            raise ValueError("windows must be ordered and non-overlapping")
        return self


class WeeklyOpening(PlaceContract):
    weekday: Annotated[int, Field(ge=0, le=6)]  # Monday=0
    schedule: DayOpening


class OpeningException(PlaceContract):
    applicable_date: date
    schedule: DayOpening
    # Trusted synthetic fixture semantics, not a model-selected authority upgrade.
    resolution: Literal["OVERRIDE", "UNRESOLVED"]


class OpeningSource(PlaceContract):
    provider: Literal["synthetic_opening_fixture"] = "synthetic_opening_fixture"
    environment: Literal["synthetic"] = "synthetic"
    source_id: Identifier
    observed_at: datetime
    expires_at: datetime
    source_reference: Annotated[str, Field(pattern=r"^synthetic-opening:[A-Za-z0-9_-]+$", max_length=100)]
    verification_state: Literal["SYNTHETIC_UNVERIFIED"] = "SYNTHETIC_UNVERIFIED"
    supplier_updated_at: None = None

    @model_validator(mode="after")
    def observation_window(self):
        if self.observed_at.utcoffset() is None or self.expires_at.utcoffset() is None:
            raise ValueError("aware source times required")
        if self.expires_at <= self.observed_at:
            raise ValueError("positive observation lifetime required")
        return self


class OpeningFacts(PlaceContract):
    schema_version: Literal["opening-facts-v1"] = "opening-facts-v1"
    fact_id: Identifier
    resource: ResourceRef
    entrance_id: Identifier | None
    applicable_scope: ApplicableScope
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    source: OpeningSource
    weekly: Annotated[tuple[WeeklyOpening, ...], Field(max_length=7)]
    exceptions: Annotated[tuple[OpeningException, ...], Field(max_length=31)]
    exceptions_complete: bool
    note: Text | None = None  # Inert; never interpreted as a rule or instruction.

    @model_validator(mode="after")
    def unambiguous(self):
        days = [w.weekday for w in self.weekly]
        dates = [e.applicable_date for e in self.exceptions]
        if len(set(days)) != len(days) or len(set(dates)) != len(dates):
            raise ValueError("duplicate weekly day or date exception")
        if any(not self.applicable_scope.start_date <= d <= self.applicable_scope.end_date for d in dates):
            raise ValueError("exception outside fact applicability")
        return self


class ActivityVisit(PlaceContract):
    item_id: Identifier
    name: Text
    kind: Literal["visit", "free_time"] = "visit"
    resource: ResourceRef | None
    entrance_id: Identifier | None = None
    start: datetime
    end: datetime
    timezone: Text = "Asia/Shanghai"
    important: bool = False
    reservation_held: bool | None = None

    @model_validator(mode="after")
    def resource_required(self):
        if self.kind == "visit" and self.resource is None:
            raise ValueError("visit requires resolved internal resource identity")
        if self.kind == "free_time" and (self.resource is not None or self.entrance_id is not None):
            raise ValueError("free time cannot bypass a place visit")
        return self


class OpeningValidationInput(PlaceContract):
    schema_version: Literal["opening-validation-input-v1"] = "opening-validation-input-v1"
    visits: Annotated[tuple[ActivityVisit, ...], Field(max_length=30)]
    facts: Annotated[tuple[OpeningFacts, ...], Field(max_length=100)]

    @model_validator(mode="after")
    def unique_ids(self):
        if len({v.item_id for v in self.visits}) != len(self.visits):
            raise ValueError("duplicate activity ID")
        if len({f.fact_id for f in self.facts}) != len(self.facts):
            raise ValueError("duplicate fact ID")
        return self


Reason = Literal[
    "free_time", "invalid_timezone", "invalid_interval", "cross_day_unsupported",
    "valid_activity_time", "resource_binding_conflict", "missing_opening_facts",
    "entrance_scope_mismatch", "date_not_applicable", "observation_in_future",
    "source_expired", "unresolved_exception", "weekday_unknown", "source_conflict",
    "closed_on_date", "outside_opening_windows", "inside_opening_window",
    "exceptions_incomplete", "admission_unknown", "after_last_entry", "admission_allowed",
    "visit_too_short", "duration_sufficient", "reservation_missing", "reservation_unknown",
    "reservation_satisfied", "other_restrictions_unknown",
]


class FeasibilityCheck(PlaceContract):
    check_id: Literal["F02.time", "F02.binding", "F02.opening", "F02.admission", "F02.duration", "F02.reservation", "F02.restrictions"]
    item_id: Identifier
    internal_resource_id: Identifier | None
    entrance_id: Identifier | None
    target_start: datetime
    target_end: datetime
    status: Status
    reason_code: Reason
    fact_ids: tuple[Identifier, ...]
    severity: Literal["info", "warning", "blocker"]
    action: Literal["none", "revise_activity", "obtain_facts", "replace_or_obtain_facts"]
    rule_version: Literal["F02-synthetic-v1"] = RULE_VERSION


def aggregate_status(states: tuple[Status, ...]) -> Status:
    """An empty/all-N/A scope is never PASS. Known independent FAIL dominates."""
    for state in ("FAIL", "UNKNOWN", "PASS"):
        if state in states:
            return state
    return "NOT_APPLICABLE"


class ValidationReport(PlaceContract):
    schema_version: Literal["opening-validation-report-v1"] = "opening-validation-report-v1"
    rule_version: Literal["F02-synthetic-v1"] = RULE_VERSION
    evaluated_at: datetime
    input_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    environment: Literal["synthetic"] = "synthetic"
    evaluated_checks: tuple[Literal["F02"]] = ("F02",)
    unimplemented_checks: tuple[Literal["F01"], Literal["F03"], Literal["F04"], Literal["F05"], Literal["F06"], Literal["F07"], Literal["F08"]] = ("F01", "F03", "F04", "F05", "F06", "F07", "F08")
    checks: tuple[FeasibilityCheck, ...]
    status: Status
    valid_until: datetime | None
    overall_trip_ready: Literal[False] = False
    usable_in_confirmed_snapshot: Literal[False] = False
    usable_for_real_trip: Literal[False] = False

    @model_validator(mode="after")
    def consistent(self):
        if self.evaluated_at.utcoffset() is None:
            raise ValueError("aware evaluation clock required")
        if self.status != aggregate_status(tuple(c.status for c in self.checks)):
            raise ValueError("incorrect report aggregate")
        if self.valid_until is not None and (self.valid_until.utcoffset() is None or self.valid_until <= self.evaluated_at):
            raise ValueError("report cannot claim an expired lifetime")
        return self


def check_visit(visit: ActivityVisit, facts: tuple[OpeningFacts, ...], now: datetime) -> tuple[FeasibilityCheck, ...]:
    """Evaluate one visit. Never select a winning source or infer missing hours."""
    checks = []

    def add(check, status, reason, used=()):
        checks.append(FeasibilityCheck(
            check_id="F02." + check, item_id=visit.item_id,
            internal_resource_id=visit.resource.internal_resource_id if visit.resource else None,
            entrance_id=visit.entrance_id, target_start=visit.start, target_end=visit.end,
            status=status, reason_code=reason, fact_ids=tuple(sorted(f.fact_id for f in used)),
            severity="blocker" if status == "FAIL" or (status == "UNKNOWN" and visit.important) else "warning" if status == "UNKNOWN" else "info",
            action="revise_activity" if status == "FAIL" else ("replace_or_obtain_facts" if visit.important else "obtain_facts") if status == "UNKNOWN" else "none"))

    if visit.kind == "free_time":
        add("opening", "NOT_APPLICABLE", "free_time")
        return tuple(checks)
    if visit.timezone != "Asia/Shanghai" or any(t.utcoffset() != timedelta(hours=8) for t in (visit.start, visit.end)):
        add("time", "FAIL", "invalid_timezone")
        return tuple(checks)
    if visit.end <= visit.start:
        add("time", "FAIL", "invalid_interval")
        return tuple(checks)
    if visit.start.date() != visit.end.date():
        add("time", "FAIL", "cross_day_unsupported")
        return tuple(checks)
    add("time", "PASS", "valid_activity_time")
    target = visit.start.date()
    related = tuple(f for f in facts if f.resource.internal_resource_id == visit.resource.internal_resource_id)
    if any(f.resource != visit.resource for f in related):
        add("binding", "FAIL", "resource_binding_conflict", related)
        return tuple(checks)
    selected = tuple(f for f in related if f.entrance_id == visit.entrance_id)
    if not selected:
        add("opening", "UNKNOWN", "entrance_scope_mismatch" if related else "missing_opening_facts", related)
        return tuple(checks)
    schedules = []
    eligible = []
    unresolved = False
    for fact in selected:
        reason = None
        if not fact.applicable_scope.start_date <= target <= fact.applicable_scope.end_date:
            reason = "date_not_applicable"
        elif now < fact.source.observed_at:
            reason = "observation_in_future"
        elif now >= fact.source.expires_at:
            reason = "source_expired"
        if reason:
            add("opening", "UNKNOWN", reason, (fact,))
            continue
        exception = next((e for e in fact.exceptions if e.applicable_date == target), None)
        if exception and exception.resolution == "UNRESOLVED":
            unresolved = True
            add("opening", "UNKNOWN", "unresolved_exception", (fact,))
            continue
        weekly = next((w for w in fact.weekly if w.weekday == target.weekday()), None)
        schedule = exception.schedule if exception else weekly.schedule if weekly else None
        if schedule is None:
            add("opening", "UNKNOWN", "weekday_unknown", (fact,))
            continue
        eligible.append(fact)
        schedules.append(schedule)
    # Unresolved contradictory evidence must not become a closed-day FAIL or PASS.
    if unresolved:
        return tuple(checks)
    if not schedules:
        return tuple(checks)
    if any(s != schedules[0] for s in schedules[1:]):
        add("opening", "UNKNOWN", "source_conflict", eligible)
        return tuple(checks)
    schedule = schedules[0]
    if schedule.closed:
        add("opening", "FAIL", "closed_on_date", eligible)
        return tuple(checks)
    # Adjacent half-open windows form a continuous opening; positive gaps do not.
    windows = []
    for window in schedule.windows:
        if windows and windows[-1][1] == window.start:
            windows[-1] = (windows[-1][0], window.end)
        else:
            windows.append((window.start, window.end))
    contained = any(a <= visit.start.time() and visit.end.time() <= b for a, b in windows)
    add("opening", "PASS" if contained else "FAIL", "inside_opening_window" if contained else "outside_opening_windows", eligible)
    if any(not f.exceptions_complete for f in eligible):
        add("opening", "UNKNOWN", "exceptions_incomplete", eligible)
    rule = schedule.admission
    if rule is None:
        add("admission", "UNKNOWN", "admission_unknown", eligible)
        return tuple(checks)
    late = rule.last_entry is not None and visit.start.time() > rule.last_entry
    add("admission", "FAIL" if late else "PASS", "after_last_entry" if late else "admission_allowed", eligible)
    short = visit.end - visit.start < timedelta(minutes=rule.minimum_visit_minutes)
    add("duration", "FAIL" if short else "PASS", "visit_too_short" if short else "duration_sufficient", eligible)
    if rule.reservation_required:
        state = "PASS" if visit.reservation_held is True else "FAIL" if visit.reservation_held is False else "UNKNOWN"
        add("reservation", state, "reservation_satisfied" if state == "PASS" else "reservation_missing" if state == "FAIL" else "reservation_unknown", eligible)
    if rule.other_restrictions:
        add("restrictions", "UNKNOWN", "other_restrictions_unknown", eligible)
    return tuple(checks)
