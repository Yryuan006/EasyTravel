"""Explicit user input and deterministic collection projection; no planner or I/O."""
from datetime import date, datetime
from typing import Annotated, Generic, Literal, TypeVar
import re

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from ...contracts import ID, Money
from .models import WorkingDraft


class InputContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


Text = Annotated[str, Field(min_length=1, max_length=500)]
T = TypeVar("T")


class MissingAnswer(InputContract):
    state: Literal["unanswered", "no_preference"]
    value: None


class ProvidedAnswer(InputContract, Generic[T]):
    state: Literal["provided"]
    value: T


type Answer[T] = Annotated[MissingAnswer | ProvidedAnswer[T], Field(discriminator="state")]


def parse_date(value):
    # FastAPI validates decoded JSON dictionaries, not model_validate_json.
    # Accept ISO dates explicitly, without numeric/datetime coercion.
    if type(value) is date:
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    raise ValueError("ISO date required")


Date = Annotated[date, BeforeValidator(parse_date)]


class FixedDates(InputContract):
    mode: Literal["fixed"]
    start_date: Date
    end_date: Date

    @model_validator(mode="after")
    def ordered(self):
        if self.end_date < self.start_date:
            raise ValueError("invalid date interval")
        return self


class DateWindow(InputContract):
    mode: Literal["window"]
    earliest_start: Date
    latest_end: Date

    @model_validator(mode="after")
    def ordered(self):
        if self.latest_end < self.earliest_start:
            raise ValueError("invalid date window")
        return self


TravelDates = Annotated[FixedDates | DateWindow, Field(discriminator="mode")]
FieldKey = Literal["origin", "destination", "dates", "days", "travelers", "target_budget_cents",
                   "hard_limit_cents", "transport", "hotel", "restrictions"]


class RequirementsInput(InputContract):
    origin: Answer[Text]
    destination: Answer[Text]
    dates: Answer[TravelDates]
    days: Answer[Annotated[int, Field(strict=True, ge=1, le=5)]]
    travelers: Answer[Annotated[int, Field(strict=True, ge=1, le=2)]]
    target_budget_cents: Answer[Money]
    hard_limit_cents: Answer[Money]
    transport: Answer[Text]
    hotel: Answer[Text]
    restrictions: Answer[Text]
    raw_text: Annotated[str, Field(min_length=1, max_length=2000)] | None

    @model_validator(mode="after")
    def nonblank(self):
        for name in ("origin", "destination", "transport", "hotel", "restrictions"):
            answer = getattr(self, name)
            if answer.state == "provided" and not answer.value.strip():
                raise ValueError("provided text must be nonblank")
        if self.raw_text is not None and not self.raw_text.strip():
            raise ValueError("raw text must be nonblank")
        return self


class CreateTripRequest(InputContract):
    requirements: RequirementsInput

    @model_validator(mode="after")
    def meaningful(self):
        if self.requirements.destination.state != "provided" and self.requirements.raw_text is None:
            raise ValueError("destination or original requirements text required")
        return self


class Provenance(InputContract):
    field: FieldKey
    source: Literal["user"]


class SuggestedValue(InputContract):
    field: FieldKey
    text: Text
    source: Literal["suggestion"]


class IntakeView(InputContract):
    intake_revision: Annotated[int, Field(strict=True, ge=1)]
    input: RequirementsInput
    collection_status: Literal["COLLECTING", "READY_TO_GENERATE"]
    blocking_fields: list[FieldKey]
    provenance: list[Provenance]
    suggested_values: list[SuggestedValue]


class Risk(InputContract):
    code: Annotated[str, Field(min_length=1, max_length=80)]
    severity: Literal["blocker", "warning"]
    message: Text
    item_ids: list[ID]


class TransportAdvice(InputContract):
    from_item_id: ID
    to_item_id: ID
    mode: Literal["public_transit", "walk"]
    text: Text
    verification: Literal["unverified"]


class GenerationNotes(InputContract):
    transport_advice: list[TransportAdvice] = Field(max_length=200)
    risks: list[Risk] = Field(max_length=100)


class DraftContext(InputContract):
    intake_revision: Annotated[int, Field(strict=True, ge=1)]
    input: RequirementsInput
    notes: GenerationNotes
    risk_rules_version: Annotated[str, Field(min_length=1, max_length=80)]


class WorkingView(WorkingDraft):
    context: DraftContext | None


class TripView(InputContract):
    """Persisted collection projection; legacy draft contexts cannot prove synchronization."""
    trip_id: ID
    created_at: datetime
    intake: IntakeView
    working: WorkingView | None
    latest_confirmed_version_id: ID | None
    synchronization: Literal["NO_DRAFT", "CURRENT", "STALE"]
    plan_confirmation: Literal["UNCONFIRMED", "CONFIRMED"]
    active_run_id: ID | None
    readiness: Literal["NOT_ASSESSED"]
    booked: Literal[False]


def collection(input: RequirementsInput):
    """Input sufficiency only; no inference from raw text, dates or preferences."""
    blocked = set()
    for field in ("origin", "destination", "travelers", "hard_limit_cents"):
        if getattr(input, field).state != "provided":
            blocked.add(field)
    if input.transport.state == "unanswered":
        blocked.add("transport")
    if input.dates.state != "provided" or input.dates.value.mode != "fixed":
        blocked.add("dates")
        if input.days.state != "provided":
            blocked.add("days")
    else:
        days = (input.dates.value.end_date - input.dates.value.start_date).days + 1
        if not 1 <= days <= 5:
            blocked.add("dates")
        if input.days.state == "provided" and input.days.value != days:
            blocked.update(("dates", "days"))
    if (input.target_budget_cents.state == input.hard_limit_cents.state == "provided"
            and input.target_budget_cents.value > input.hard_limit_cents.value):
        blocked.update(("target_budget_cents", "hard_limit_cents"))
    ordered = [name for name in RequirementsInput.model_fields if name in blocked]
    return ordered


def trip_view(trip_id, created_at, revision, input, working=None, context=None, active_run_id=None, confirmed_revision=None):
    if working is not None:
        working = WorkingView(**working.model_dump(), context=context)
    blocked = collection(input)
    return TripView(trip_id=trip_id, created_at=created_at,
        intake=IntakeView(intake_revision=revision, input=input,
            collection_status="COLLECTING" if blocked else "READY_TO_GENERATE",
            blocking_fields=blocked, provenance=[Provenance(field=name, source="user")
                for name in RequirementsInput.model_fields if name != "raw_text"
                and getattr(input, name).state != "unanswered"], suggested_values=[]),
        working=working, latest_confirmed_version_id=working.latest_confirmed_version_id if working else None,
        synchronization=("CURRENT" if context and context.intake_revision == revision else "STALE") if working else "NO_DRAFT",
        plan_confirmation=("CONFIRMED" if working and context and context.intake_revision == revision
                           and confirmed_revision == working.revision else "UNCONFIRMED"),
        active_run_id=active_run_id, readiness="NOT_ASSESSED", booked=False)


Revision = Annotated[int, Field(strict=True, ge=1)]


class UpdateRequirementsRequest(InputContract):
    expected_intake_revision: Revision
    expected_working_revision: Revision | None
    requirements: RequirementsInput


class AnswerRequest(InputContract):
    expected_intake_revision: Revision
    expected_working_revision: Revision | None
    field_key: FieldKey
    answer: Answer[Text | Money | TravelDates]

    @model_validator(mode="after")
    def field_type(self):
        # Reuse the exact full-input validators, including strict numeric bounds.
        data = {name: {"state": "unanswered", "value": None}
                for name in RequirementsInput.model_fields if name != "raw_text"}
        data["raw_text"] = None
        data[self.field_key] = self.answer.model_dump(mode="json")
        RequirementsInput.model_validate(data)
        return self


class ClarificationQuestion(InputContract):
    question_id: ID
    intake_revision: Revision
    field_key: FieldKey
    prompt: Text
    allow_no_preference: bool


class NextQuestionResponse(InputContract):
    intake_revision: Revision
    question: ClarificationQuestion | None
    blocking_fields: list[FieldKey]


# Versioned rule priority: destination/date ambiguity, party, budget, transport.
# Origin is required for transport; optional hotel/restrictions are never inferred.
QUESTION_PRIORITY = ("destination", "dates", "days", "origin", "travelers",
                     "target_budget_cents", "hard_limit_cents", "transport")

def next_field(input):
    blocked = collection(input)
    return next((field for field in QUESTION_PRIORITY if field in blocked), None)
