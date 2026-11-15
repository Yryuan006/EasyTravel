"""Intent -> durable run -> immutable proposal -> atomic approve/reject."""
from datetime import datetime, timezone
from uuid import uuid4
from pydantic import ValidationError
from ...model_adapter import generate_output, OutputFailure
from ...rules import evaluate_budget
from ..domain.models import SafeChangeOutput, ChangeProposal, SpecificAuthorization, WorkingDraft
from ..domain.changes import ProposalContext, ProposalView, ItemDiff, DecisionResult
from ..domain.intake import DraftContext, GenerationNotes, WorkingView
from ..domain.confirmation import preview as confirmation_preview, UnknownCost
from ..domain.hashing import digest
from ..domain.rules import construct_candidate, check_constraints, ChangeError
from ..domain.errors import ResourceNotFound
from .plans import PlanService
from .generations import GenerationService, failure
from ..domain.replanning import ReplanProposal,validate_v2


def validate_change(working, output, *, now):
    candidate = construct_candidate(working,output)
    req = working.requirements
    if any(i.start.date()<req.start_date or i.end.date()>req.end_date or
           (i.kind=='activity' and i.start.date()!=i.end.date()) for i in candidate.payload.items):
        raise ChangeError('invalid_output')
    hotels=[i for i in candidate.payload.items if i.kind=='hotel']
    nights=(req.end_date-req.start_date).days
    if (nights and (len(hotels)!=1 or hotels[0].check_in!=req.start_date or hotels[0].check_out!=req.end_date or hotels[0].rooms!=1)) or (not nights and hotels):
        raise ChangeError('invalid_output')
    if any(not c.is_mock or c.evidence_id is not None or c.category=='quote' for c in candidate.payload.costs):
        raise ChangeError('invalid_output')
    return candidate,check_constraints(candidate.payload,req,now=now)


class ChangeService:
    def __init__(self,uow,*,now=lambda:datetime.now(timezone.utc)):
        self.uow,self.now=uow,now
        self.plans=PlanService(uow,now=now)
        self.generations=GenerationService(uow,now=now)

    def check_current(self,trip,request):
        if trip.intake.intake_revision!=request.expected_intake_revision:
            raise ChangeError('stale_intake')
        if not trip.working:
            raise ChangeError('draft_not_ready')
        if trip.working.revision!=request.expected_working_revision:
            raise ChangeError('stale_revision')
        if trip.synchronization!='CURRENT' or trip.working.context is None:
            raise ChangeError('scope_replan_not_supported')

    async def accept(self,trip_id,request,*,key,actor):
        PlanService.identifier(actor)
        path=f'/api/v1/trips/{trip_id}/change-intents';request_hash=digest(request.model_dump(mode='json'))
        async with self.uow(actor) as repo:
            await repo.lock_command(digest(dict(actor=actor,method='POST',path=path,key=key)))
            replay=await repo.replay_change(key,request_hash,path,intent=True)
            if replay is not None:return replay
            await repo.lock_trip(trip_id)
            trip=await repo.read_trip(trip_id);self.check_current(trip,request)
            if trip.active_run_id:raise ChangeError('generation_in_progress')
            from .retries import retry_attempt
            attempt=await retry_attempt(repo,trip_id,request,'CHANGE_INTENT')
            result=await repo.accept_change(trip,request,str(uuid4()),attempt)
            await repo.save_change_command(key,request_hash,result,trip_id,str(uuid4()),path,202)
            return result

    async def execute(self,run,*,actor,adapter_factory):
        async with self.uow(actor) as repo:
            working,intent=await repo.frozen_change(run)
        try:
            output=await generate_output(adapter_factory(working,intent),SafeChangeOutput)
            candidate,budget=validate_change(working,output,now=self.now())
            # Keep source warnings additive; regenerate required system risks through shared disclosure rules.
            ids={i.id for i in candidate.payload.items}
            # Recompute system warnings, while keeping every saved blocker additive.
            regenerated={'unknown_costs','mock_data','prices_unverified','route_unverified','not_booked'}
            risks=[r.model_validate(r.model_dump() | dict(item_ids=[i for i in r.item_ids if i in ids]))
                   for r in working.context.notes.risks if r.severity=='blocker' or r.code not in regenerated]
            notes=GenerationNotes(transport_advice=[],risks=risks)
            context=DraftContext(intake_revision=run.intake_revision,input=working.context.input,notes=notes,risk_rules_version='mock-change-v1')
            async with self.uow(actor) as repo:
                await repo.lock_trip(run.trip_id)
                current=await repo.run(run.trip_id,run.run_id,lock=True)
                if current.status!='RUNNING':return
                trip=await repo.read_trip(run.trip_id)
                if trip.intake.intake_revision!=run.intake_revision or not trip.working or trip.working.revision!=run.base_working_revision or trip.synchronization!='CURRENT':
                    await repo.finish(run,error=failure('stale_input'));return
                # Repeat validation against the current immutable base under the Trip lock.
                candidate,budget=validate_change(trip.working,output,now=self.now())
                preview,_=confirmation_preview(trip.model_copy(update={'working':WorkingView(**working.model_dump(exclude={'context'}) | dict(payload=candidate.payload,context=context))}),now=self.now())
                notes=GenerationNotes(transport_advice=[],risks=preview.risks)
                context=DraftContext(**context.model_dump(exclude={'notes'}),notes=notes)
                data=dict(proposal_id=str(uuid4()),trip_id=run.trip_id,owner_id=actor,
                    requirements_revision=working.requirements.requirements_revision,output=output.model_dump(mode='json'),
                    candidate=candidate.payload.model_dump(mode='json'),level=candidate.level,touched_item_ids=list(candidate.touched))
                proposal=ChangeProposal.model_validate(data|dict(candidate_hash=digest(data)))
                before={i.id:i for i in working.payload.items};after={i.id:i for i in candidate.payload.items}
                differences=[ItemDiff(item_id=i,before=before.get(i),after=after.get(i),
                    before_costs=[c for c in working.payload.costs if c.item_id==i],after_costs=[c for c in candidate.payload.costs if c.item_id==i]) for i in candidate.touched]
                pc=ProposalContext(source=working.context,candidate_context=context,differences=differences,
                    source_budget=evaluate_budget(working.payload,working.requirements,{},now=self.now()),candidate_budget=budget)
                await repo.finish_proposal(run,proposal,pc)
        except (OutputFailure,ValidationError):
            await self.generations.fail(run,'invalid_output',actor=actor)
        except ChangeError as error:
            code=str(error) if str(error) in ('unsupported_change_intent','known_costs_exceed_hard_limit') else 'invalid_output'
            await self.generations.fail(run,code,actor=actor)

    async def get(self,trip_id,proposal_id,*,actor):
        PlanService.identifier(actor)
        async with self.uow(actor) as repo:
            await repo.read_only()
            trip=await repo.read_trip(trip_id)
            proposal=await repo.proposal(proposal_id)
            if proposal.trip_id!=trip_id:raise ResourceNotFound()
            v2=await repo.replan_context(proposal_id)
            if v2:validate_v2(proposal,v2)
            else:self.plans.validate_proposal(proposal)
            context,_=await repo.proposal_context(trip_id,proposal_id)
            decision=await repo.decision(trip_id,proposal_id)
            stale=trip.working.revision!=proposal.output.base_working_revision or trip.intake.intake_revision!=(v2.base_intake_revision if v2 else context.source.intake_revision) or trip.synchronization!=('STALE' if v2 else 'CURRENT')
            return ProposalView(proposal=proposal,context_v2=v2,candidate_notes=context.candidate_context.notes,
                state=decision.state if decision else 'STALE' if stale else 'PENDING',budget=context.candidate_budget,
                risks=context.candidate_context.notes.risks,requires_specific_authorization=proposal.level=='L3',
                intake_revision=v2.base_intake_revision if v2 else context.source.intake_revision,source_input=context.source.input,differences=context.differences,source_budget=context.source_budget,
                unknown_costs=[UnknownCost(id=c.id,reason=c.unknown_reason) for c in proposal.candidate.costs if c.amount_cents is None])

    async def decide(self,trip_id,proposal_id,request,*,key,actor):
        PlanService.identifier(actor)
        path=f'/api/v1/trips/{trip_id}/proposals/{proposal_id}/decisions';request_hash=digest(request.model_dump(mode='json'))
        async with self.uow(actor) as repo:
            await repo.lock_command(digest(dict(actor=actor,method='POST',path=path,key=key)))
            replay=await repo.replay_change(key,request_hash,path)
            if replay is not None:return replay
            operation_id=str(uuid4());await repo.lock_operation(operation_id)
            await repo.lock_trip(trip_id)
            trip=await repo.read_trip(trip_id)
            proposal=await repo.proposal(proposal_id)
            if proposal.trip_id!=trip_id:raise ResourceNotFound()
            if not isinstance(proposal,ReplanProposal):self.plans.validate_proposal(proposal)
            context,run_id=await repo.proposal_context(trip_id,proposal_id)
            if await repo.decision(trip_id,proposal_id):raise ChangeError('proposal_already_decided')
            if isinstance(proposal,ReplanProposal):
                from .replanning import ReplanService
                return await ReplanService(self.uow,now=self.now).decide_in_transaction(repo,trip,proposal,context,run_id,request,key=key,request_hash=request_hash,operation_id=operation_id,path=path,actor=actor)
            self.check_current(trip,request)
            if context.source.intake_revision!=trip.intake.intake_revision or proposal.output.base_working_revision!=trip.working.revision:
                raise ChangeError('stale_proposal')
            if request.seen_candidate_hash!=proposal.candidate_hash:raise ChangeError('candidate_mismatch')
            if context.candidate_context.risk_rules_version!='mock-change-v1' or context.source!=trip.working.context:
                raise ChangeError('proposal_context_mismatch')
            grant=None
            if request.decision=='approve':
                candidate,budget=validate_change(trip.working,proposal.output,now=self.now())
                if candidate.payload!=proposal.candidate or candidate.touched!=proposal.touched_item_ids or candidate.level!=proposal.level:
                    raise ChangeError('candidate_mismatch')
                if any(r.severity=='blocker' for r in context.candidate_context.notes.risks):raise ChangeError('blocking_risks')
                if tuple(sorted(request.item_ids))!=candidate.touched:raise ChangeError('authorization_scope_mismatch')
                if candidate.level=='L3':
                    if request.explicit_authorization is not True:raise ChangeError('specific_authorization_required')
                    grant=SpecificAuthorization(authorization_id=str(uuid4()),trip_id=trip_id,proposal_id=proposal_id,
                        candidate_hash=proposal.candidate_hash,base_working_revision=trip.working.revision,item_ids=candidate.touched,granted_by=actor,granted_at=self.now())
                    await repo.save_authorization(grant)
                elif request.explicit_authorization is not False:raise ChangeError('unexpected_authorization')
                applied=await self.plans.apply_in_transaction(repo,proposal_id,operation_id=operation_id,actor=actor,authorization_id=grant.authorization_id if grant else None)
                await repo.attach_change_context(trip_id,applied['working_revision'],context.candidate_context,run_id)
                revision=applied['working_revision']
            else:
                revision=trip.working.revision
                await repo.save_receipt(operation_id,trip_id,'reject_proposal',request_hash,dict(proposal_id=proposal_id,decision='reject'))
            result=DecisionResult(proposal_id=proposal_id,decision=request.decision,state='APPLIED' if request.decision=='approve' else 'REJECTED',
                operation_id=operation_id,working_revision=revision,latest_confirmed_version_id=trip.latest_confirmed_version_id)
            await repo.save_decision(trip_id,result,operation_id,request_hash)
            await repo.save_change_command(key,request_hash,result,trip_id,operation_id,path,200)
            return result
