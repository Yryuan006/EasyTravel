"""Untrusted model DTOs. Authority and computed conclusions never belong here."""
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

ID = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]
Text = Annotated[str, Field(min_length=1, max_length=500)]
Money = Annotated[int, Field(strict=True, ge=0, le=10**12)]
Revision = Annotated[int, Field(strict=True, ge=1)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TripRequirements(Contract):
    destination: Text
    start_date: date
    end_date: date
    timezone: Literal["Asia/Shanghai"]
    currency: Literal["CNY"]
    hard_limit_cents: Money
    requirements_revision: Revision

    @model_validator(mode="after")
    def dates_valid(self):
        if self.end_date < self.start_date:
            raise ValueError("trip end precedes start")
        return self


class PlanItem(Contract):
    id: ID
    kind: Literal["hotel", "activity"]
    resource_id: ID
    title: Text
    start: datetime
    end: datetime
    room_type: Text | None = None
    check_in: date | None = None
    check_out: date | None = None
    rooms: Annotated[int, Field(strict=True, ge=1, le=20)] | None = None

    @model_validator(mode="after")
    def interval_valid(self):
        if any(t.utcoffset() != timedelta(hours=8) for t in (self.start, self.end)):
            raise ValueError("explicit Asia/Shanghai offset required")
        if self.end <= self.start:
            raise ValueError("item end must follow start")
        hotel_fields = (self.room_type, self.check_in, self.check_out, self.rooms)
        if self.kind == "hotel":
            if any(v is None for v in hotel_fields):
                raise ValueError("hotel room and stay details required")
            if (self.check_in != self.start.date() or self.check_out != self.end.date()
                    or self.check_out <= self.check_in):
                raise ValueError("hotel stay dates must match interval")
        elif any(v is not None for v in hotel_fields):
            raise ValueError("activity cannot contain hotel fields")
        return self


class PriceQuery(Contract):
    """Exact scope of an already expanded CNY total; no room/night arithmetic."""
    resource_id: ID
    start_date: date
    end_date: date
    currency: Literal["CNY"]
    room_type: Text | None
    rooms: Annotated[int, Field(strict=True, ge=1, le=20)] | None

    @model_validator(mode="after")
    def valid_scope(self):
        if self.end_date < self.start_date:
            raise ValueError("invalid price dates")
        if (self.room_type is None) != (self.rooms is None):
            raise ValueError("room type and count must be supplied together")
        return self


class CostLine(Contract):
    id: ID
    item_id: ID
    amount_cents: Money | None
    category: Literal["quote", "estimate", "unknown"]
    unknown_reason: Text | None
    query: PriceQuery
    evidence_id: ID | None
    is_mock: StrictBool

    @model_validator(mode="after")
    def unknown_explicit(self):
        if self.category == "unknown":
            if self.amount_cents is not None or self.unknown_reason is None:
                raise ValueError("unknown cost requires null amount and reason")
        elif self.amount_cents is None or self.unknown_reason is not None:
            raise ValueError("known cost requires amount and null unknown reason")
        return self


class Evidence(Contract):
    """Server-owned record, never accepted from a model response."""
    id: ID
    source_type: Literal["mock", "supplier_quote"]
    query: PriceQuery
    obtained_at: datetime
    valid_until: datetime
    amount_cents: Money

    @model_validator(mode="after")
    def valid_times(self):
        if any(t.utcoffset() is None for t in (self.obtained_at, self.valid_until)):
            raise ValueError("evidence timestamps must be timezone aware")
        if self.valid_until <= self.obtained_at:
            raise ValueError("invalid evidence validity interval")
        return self


class PlanDraftOutput(Contract):
    items: Annotated[tuple[PlanItem, ...], Field(min_length=1, max_length=100)]
    costs: Annotated[tuple[CostLine, ...], Field(min_length=1, max_length=300)]

    @model_validator(mode="after")
    def references_valid(self):
        items = {i.id: i for i in self.items}
        if len(items) != len(self.items) or len({c.id for c in self.costs}) != len(self.costs):
            raise ValueError("duplicate item or cost ID")
        if {c.item_id for c in self.costs} != set(items):
            raise ValueError("every item needs a cost line; all references must exist")
        for cost in self.costs:
            item = items[cost.item_id]
            expected = PriceQuery(resource_id=item.resource_id, start_date=item.start.date(),
                                  end_date=item.end.date(), currency="CNY",
                                  room_type=item.room_type, rooms=item.rooms)
            if cost.query != expected:
                raise ValueError("cost query does not match item")
        return self


class ReplaceActivity(Contract):
    operation: Literal["replace_activity"]
    target_item_id: ID
    replacement: PlanItem
    costs: Annotated[tuple[CostLine, ...], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def activity_only(self):
        if self.replacement.kind != "activity" or self.replacement.id != self.target_item_id:
            raise ValueError("only stable-ID activity replacement supported")
        PlanDraftOutput(items=(self.replacement,), costs=self.costs)
        return self


class ChangeProposalOutput(Contract):
    base_working_revision: Revision
    explanation: Text
    operations: Annotated[tuple[ReplaceActivity, ...], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def unique_targets(self):
        if len({o.target_item_id for o in self.operations}) != len(self.operations):
            raise ValueError("duplicate operation target")
        ids = [c.id for o in self.operations for c in o.costs]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate proposal cost ID")
        return self
