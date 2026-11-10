"""Pure candidate construction, permission classification and full constraints."""
from dataclasses import dataclass
from datetime import datetime

from ...contracts import PlanDraftOutput, ReplaceActivity
from ...rules import evaluate_budget
from .errors import BusinessConflict
from .hashing import digest
from .models import AddItem, DeleteItem, ItemConfirmation, ReplaceHotel, SafeChangeOutput, WorkingDraft

class ChangeError(BusinessConflict):
    """Stable business error code."""


def item_hash(item):
    return digest(item.model_dump(mode="json"))


def validate_confirmations(working: WorkingDraft):
    items = {item.id: item for item in working.payload.items}
    seen = set()
    for confirmation in working.item_confirmations:
        if (confirmation.item_id in seen or confirmation.item_id not in items
                or item_hash(items[confirmation.item_id]) != confirmation.item_hash):
            raise ChangeError("invalid_item_confirmation")
        seen.add(confirmation.item_id)


@dataclass(frozen=True)
class Candidate:
    payload: PlanDraftOutput
    level: str
    touched: tuple[str, ...]
    remaining_confirmations: tuple[ItemConfirmation, ...]


def construct_candidate(working: WorkingDraft, output: SafeChangeOutput) -> Candidate:
    validate_confirmations(working)
    if output.base_working_revision != working.revision:
        raise ChangeError("stale_proposal")
    originals = {item.id: item for item in working.payload.items}
    items = dict(originals)
    costs = list(working.payload.costs)
    touched = set()
    for operation in output.operations:
        if isinstance(operation, AddItem):
            if operation.item.id in items:
                raise ChangeError("item_id_already_exists")
            items[operation.item.id] = operation.item
            costs.extend(operation.costs)
            touched.add(operation.item.id)
            continue
        target = operation.target_item_id
        if target not in items:
            raise ChangeError("target_not_found")
        old = items.pop(target)
        touched.add(target)
        costs = [cost for cost in costs if cost.item_id != target]
        if isinstance(operation, DeleteItem):
            continue
        if (isinstance(operation, ReplaceActivity) and old.kind != "activity"
                or isinstance(operation, ReplaceHotel) and old.kind != "hotel"):
            raise ChangeError("wrong_operation_kind")
        replacement = operation.replacement
        if replacement.id in items:
            raise ChangeError("replacement_id_already_exists")
        items[replacement.id] = replacement
        costs.extend(operation.costs)
        touched.add(replacement.id)
    # Full schema validation: no model_copy(update=...), duplicates or dangling costs.
    candidate = PlanDraftOutput.model_validate(dict(
        items=[item.model_dump(mode="json") for item in items.values()],
        costs=[cost.model_dump(mode="json") for cost in costs]))
    before = {i.id: i.model_dump(mode="json") for i in working.payload.items}
    after = {i.id: i.model_dump(mode="json") for i in candidate.items}
    old_costs = {c.id: c.model_dump(mode="json") for c in working.payload.costs}
    new_costs = {c.id: c.model_dump(mode="json") for c in candidate.costs}
    if before == after and old_costs == new_costs:
        raise ChangeError("no_change")
    confirmed_ids = {c.item_id for c in working.item_confirmations}
    hotels = {i.id for i in (*working.payload.items, *candidate.items) if i.kind == "hotel"}
    if touched & (confirmed_ids | hotels):
        level = "L3"
    else:
        # Smallest supported L1: title edits only, with all costs unchanged.
        title_only = set(before) == set(after) and old_costs == new_costs and all(
            {k: v for k, v in before[key].items() if k != "title"}
            == {k: v for k, v in after[key].items() if k != "title"} for key in before)
        level = "L1" if title_only else "L2"
    remaining = tuple(c for c in working.item_confirmations if c.item_id not in touched)
    return Candidate(candidate, level, tuple(sorted(touched)), remaining)


def check_constraints(payload, requirements, *, now: datetime):
    activities = sorted((i for i in payload.items if i.kind == "activity"), key=lambda i: i.start)
    if any(left.end > right.start for left, right in zip(activities, activities[1:])):
        raise ChangeError("activity_overlap")
    budget = evaluate_budget(payload, requirements, {}, now=now)
    if budget.constraint == "OVER_LIMIT":
        raise ChangeError("known_costs_exceed_hard_limit")
    return budget
