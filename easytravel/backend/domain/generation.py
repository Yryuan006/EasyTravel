"""Generation API records and deterministic first-draft constraints."""
from datetime import datetime, timedelta
from typing import Literal
from pydantic import Field, model_validator
from ...contracts import Contract, ID, Revision, TripRequirements
from .intake import InputContract
from .rules import ChangeError, check_constraints
from ...rules import BudgetResult

class GenerateRequest(InputContract):
    expected_intake_revision: Revision
    expected_working_revision: Revision | None
    retry_of: ID | None

class RunResult(InputContract):
    working_revision: Revision | None
    proposal_id: ID | None

class RunError(InputContract):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)
    retryable: bool

class GenerationRun(Contract):
    run_id: ID
    trip_id: ID
    kind: Literal["INITIAL_PLAN", "CHANGE_INTENT"]
    intake_revision: Revision
    base_working_revision: Revision | None
    status: Literal["ACCEPTED", "RUNNING", "SUCCEEDED", "FAILED"]
    attempt: Revision
    retry_of: ID | None
    created_at: datetime
    updated_at: datetime
    result: RunResult | None
    error: RunError | None
    diagnostics_budget: BudgetResult | None

    @model_validator(mode='after')
    def paired_result(self):
        if (self.kind=='INITIAL_PLAN') != (self.base_working_revision is None):
            raise ValueError('run base mismatch')
        if self.status=='SUCCEEDED':
            if self.result is None or self.error is not None:
                raise ValueError('missing run result')
            if self.kind=='INITIAL_PLAN':
                if self.result.working_revision!=1 or self.result.proposal_id is not None:raise ValueError('initial result mismatch')
            elif self.result.working_revision is not None or self.result.proposal_id is None:raise ValueError('proposal result mismatch')
        elif self.result is not None or (self.status=='FAILED') != (self.error is not None):
            raise ValueError('run state mismatch')
        return self


def requirements_from(input):
    return TripRequirements(destination=input.destination.value,
        start_date=input.dates.value.start_date, end_date=input.dates.value.end_date,
        timezone="Asia/Shanghai", currency="CNY", hard_limit_cents=input.hard_limit_cents.value,
        requirements_revision=1)


def validate_initial(payload, requirements, *, now):
    # In addition to the shared reference/time/budget rules, require complete day/stay coverage.
    dates = {requirements.start_date + timedelta(days=n) for n in range((requirements.end_date-requirements.start_date).days+1)}
    activities = [i for i in payload.items if i.kind == "activity"]
    hotels = [i for i in payload.items if i.kind == "hotel"]
    if ({i.start.date() for i in activities} != dates or
        any(i.start.date() != i.end.date() for i in activities) or
        any(i.start.date() < requirements.start_date or i.end.date() > requirements.end_date for i in payload.items)):
        raise ChangeError("invalid_output")
    nights = len(dates)-1
    if (nights and (len(hotels) != 1 or hotels[0].check_in != requirements.start_date or
        hotels[0].check_out != requirements.end_date or hotels[0].rooms != 1)) or (not nights and hotels):
        raise ChangeError("invalid_output")
    # Mock provenance is enforced by the application, never believed from model claims.
    if any(not c.is_mock or c.evidence_id is not None or c.category == "quote" for c in payload.costs):
        raise ChangeError("invalid_output")
    return check_constraints(payload, requirements, now=now)


class GenerationState(InputContract):
    trip_id: ID
    working_revision: Revision | None
    source_run: GenerationRun | None
    latest_run: GenerationRun | None
