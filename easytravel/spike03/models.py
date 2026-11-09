"""Separate untrusted change DTOs from server-owned records and authorization."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ..contracts import (Contract, ID, Revision, Text, PlanDraftOutput, PlanItem,
                         CostLine, ReplaceActivity, TripRequirements)
from ..rules import BudgetResult


class ReplaceHotel(Contract):
    operation: Literal["replace_hotel"]
    target_item_id: ID
    replacement: PlanItem
    costs: Annotated[tuple[CostLine, ...], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def valid_hotel(self):
        if self.replacement.kind != "hotel":
            raise ValueError("hotel replacement must be a hotel")
        PlanDraftOutput(items=(self.replacement,), costs=self.costs)
        return self


class DeleteItem(Contract):
    operation: Literal["delete_item"]
    target_item_id: ID


class AddItem(Contract):
    operation: Literal["add_item"]
    item: PlanItem
    costs: Annotated[tuple[CostLine, ...], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def valid_item(self):
        PlanDraftOutput(items=(self.item,), costs=self.costs)
        return self


Operation = Annotated[ReplaceActivity | ReplaceHotel | DeleteItem | AddItem, Field(discriminator="operation")]


class SafeChangeOutput(Contract):
    base_working_revision: Revision
    explanation: Text
    operations: Annotated[tuple[Operation, ...], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def unique_targets(self):
        targets = [op.item.id if isinstance(op, AddItem) else op.target_item_id for op in self.operations]
        if len(targets) != len(set(targets)):
            raise ValueError("one operation per item; delete/recreate same ID is unsupported")
        return self


class ItemConfirmation(Contract):
    item_id: ID
    item_hash: str


class WorkingDraft(Contract):
    trip_id: ID
    owner_id: Literal["demo_owner"]
    revision: Revision
    requirements: TripRequirements
    payload: PlanDraftOutput
    item_confirmations: tuple[ItemConfirmation, ...]
    latest_confirmed_version_id: ID | None


class ConfirmedSnapshot(Contract):
    snapshot_id: ID
    trip_id: ID
    source_working_revision: Revision
    requirements: TripRequirements
    payload: PlanDraftOutput
    item_confirmations: tuple[ItemConfirmation, ...]
    disclosure_hash: str
    budget: BudgetResult
    confirmed_by: Literal["demo_owner"]
    confirmed_at: datetime


class ChangeProposal(Contract):
    proposal_id: ID
    trip_id: ID
    owner_id: Literal["demo_owner"]
    requirements_revision: Revision
    output: SafeChangeOutput
    candidate: PlanDraftOutput
    level: Literal["L1", "L2", "L3"]
    touched_item_ids: tuple[ID, ...]
    candidate_hash: str


class SpecificAuthorization(Contract):
    authorization_id: ID
    trip_id: ID
    proposal_id: ID
    candidate_hash: str
    base_working_revision: Revision
    item_ids: tuple[ID, ...]
    granted_by: Literal["demo_owner"]
    granted_at: datetime
