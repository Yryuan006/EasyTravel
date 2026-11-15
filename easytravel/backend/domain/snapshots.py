"""Historical summaries contain stored facts only."""
from datetime import datetime
from typing import Annotated
from pydantic import Field
from ...contracts import ID, Revision, Contract
from ...rules import BudgetResult
from .intake import InputContract


class SnapshotSummary(Contract):
    snapshot_id: ID
    source_working_revision: Revision
    confirmed_at: datetime
    budget: BudgetResult


class SnapshotPage(InputContract):
    items: Annotated[list[SnapshotSummary], Field(max_length=100)]
    next_cursor: Annotated[str, Field(min_length=1, max_length=500)] | None
