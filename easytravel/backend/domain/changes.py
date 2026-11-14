"""Version-bound change review contracts; existing v1 proposal/hash stays unchanged."""
from typing import Annotated, Literal
from pydantic import Field, StrictBool, model_validator
from ...contracts import ID, Revision, PlanItem, CostLine
from ...rules import BudgetResult
from .confirmation import Hash, UnknownCost
from .intake import InputContract, DraftContext, GenerationNotes, Risk
from .models import ChangeProposal


class ChangeIntentRequest(InputContract):
    expected_intake_revision: Revision
    expected_working_revision: Revision
    intent: Annotated[str, Field(min_length=1, max_length=2000)]
    retry_of: ID | None


class RejectRequest(InputContract):
    expected_intake_revision: Revision
    expected_working_revision: Revision
    seen_candidate_hash: Hash
    decision: Literal['reject']


class ApproveRequest(InputContract):
    expected_intake_revision: Revision
    expected_working_revision: Revision
    seen_candidate_hash: Hash
    decision: Literal['approve']
    item_ids: Annotated[list[ID], Field(min_length=1, max_length=200)]
    explicit_authorization: StrictBool

    @model_validator(mode='after')
    def unique_scope(self):
        if len(set(self.item_ids)) != len(self.item_ids):
            raise ValueError('duplicate scope')
        return self


DecisionRequest = Annotated[ApproveRequest | RejectRequest, Field(discriminator='decision')]


class ItemDiff(InputContract):
    item_id: ID
    before: PlanItem | None
    after: PlanItem | None
    before_costs: list[CostLine]
    after_costs: list[CostLine]


class ProposalContext(InputContract):
    source: DraftContext
    candidate_context: DraftContext
    differences: list[ItemDiff]
    source_budget: BudgetResult
    candidate_budget: BudgetResult


class ProposalView(InputContract):
    proposal: ChangeProposal
    context_v2: None
    candidate_notes: GenerationNotes
    state: Literal['PENDING', 'APPLIED', 'REJECTED', 'STALE']
    budget: BudgetResult
    risks: list[Risk]
    requires_specific_authorization: bool
    # Additive read-only review facts absent from the early M1-00 draft.
    intake_revision: Revision
    differences: list[ItemDiff]
    source_budget: BudgetResult
    unknown_costs: list[UnknownCost]


class DecisionResult(InputContract):
    proposal_id: ID
    decision: Literal['approve', 'reject']
    state: Literal['APPLIED', 'REJECTED']
    operation_id: ID | None
    working_revision: Revision
    latest_confirmed_version_id: ID | None
