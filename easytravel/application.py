"""The mock and future vendor adapters share this draft evaluation entry point."""
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from .contracts import Evidence, PlanDraftOutput, TripRequirements
from .model_adapter import ModelAdapter, generate_output
from .rules import BudgetResult, evaluate_budget


@dataclass(frozen=True)
class EvaluatedDraft:
    draft: PlanDraftOutput
    budget: BudgetResult


async def generate_draft(adapter: ModelAdapter, requirements: TripRequirements,
                         evidence: Mapping[str, Evidence], *, now: datetime,
                         timeout_seconds: float = 1) -> EvaluatedDraft:
    draft = await generate_output(adapter, PlanDraftOutput, timeout_seconds=timeout_seconds)
    return EvaluatedDraft(draft, evaluate_budget(draft, requirements, evidence, now=now))
