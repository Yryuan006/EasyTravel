"""Independent v2 full-plan contract and digest; never interpreted as v1 operations."""
from typing import Literal
from ...contracts import Contract, ID, Revision, Text, PlanDraftOutput, TripRequirements
from .intake import InputContract, RequirementsInput, GenerationNotes
from .confirmation import Hash
from .hashing import digest
from .rules import ChangeError


class ReplanOutput(Contract):
    kind: Literal['requirements_replan']
    base_working_revision: Revision
    explanation: Text


class ReplanProposal(Contract):
    proposal_id: ID
    trip_id: ID
    owner_id: ID
    requirements_revision: Revision
    output: ReplanOutput
    candidate: PlanDraftOutput
    level: Literal['L3']
    touched_item_ids: tuple[ID, ...]
    candidate_hash: Hash


class ProposalContextV2(InputContract):
    schema_version: Literal[2]
    base_intake_revision: Revision
    candidate_requirements: TripRequirements
    candidate_input: RequirementsInput
    notes: GenerationNotes
    risk_hash: Hash


def risk_hash(context):
    return digest(dict(version='replan-risk-v2',context=context.model_dump(mode='json',exclude={'risk_hash'})))


def candidate_hash(proposal,context):
    data=proposal if isinstance(proposal,dict) else proposal.model_dump(mode='json',exclude={'candidate_hash'})
    return digest(dict(version='requirements-replan-v2',proposal=data,context=context.model_dump(mode='json')))


def validate_v2(proposal,context):
    if (not isinstance(proposal,ReplanProposal) or context is None or context.risk_hash!=risk_hash(context)
            or proposal.candidate_hash!=candidate_hash(proposal,context)):
        raise ChangeError('proposal_integrity_error')
