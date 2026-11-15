"""Full requirements replanning; v2 approval stays in the outer decision transaction."""
from datetime import datetime,timezone
from uuid import uuid4
from pydantic import ValidationError
from ...contracts import PlanDraftOutput,TripRequirements
from ...model_adapter import generate_output,OutputFailure
from ...rules import evaluate_budget
from ..domain.generation import requirements_from,validate_initial
from ..domain.replanning import ReplanProposal,ProposalContextV2,candidate_hash,risk_hash,validate_v2
from ..domain.intake import DraftContext,GenerationNotes,Risk,WorkingView
from ..domain.models import WorkingDraft,SpecificAuthorization
from ..domain.changes import ProposalContext,ItemDiff,DecisionResult
from ..domain.confirmation import preview
from ..domain.rules import ChangeError,item_hash
from .generations import GenerationService,failure
from .plans import budget_data


def new_requirements(input,source):
    return TripRequirements(**requirements_from(input).model_dump(exclude={'requirements_revision'}),requirements_revision=source.requirements.requirements_revision+1)


class ReplanService:
    def __init__(self,uow,*,now=lambda:datetime.now(timezone.utc)):
        self.uow,self.now=uow,now

    async def execute(self,run,*,actor,adapter_factory):
        async with self.uow(actor) as repo:
            source,input=await repo.frozen_replan(run)
        requirements=new_requirements(input,source)
        try:
            payload=await generate_output(adapter_factory(input),PlanDraftOutput)
            budget=validate_initial(payload,requirements,now=self.now())
            notes=GenerationNotes(transport_advice=[],risks=[Risk(code='mock_replan',severity='warning',message='按新需求生成的 Mock 规划；交通、住宿、人数及偏好未由真实供应商核验，不代表已预订。',item_ids=[i.id for i in payload.items])]+[r for r in source.context.notes.risks if r.severity=='blocker'])
            dc=DraftContext(intake_revision=run.intake_revision,input=input,notes=notes,risk_rules_version='requirements-replan-v2')
            async with self.uow(actor) as repo:
                await repo.lock_trip(run.trip_id)
                current=await repo.run(run.trip_id,run.run_id,lock=True)
                if current.status!='RUNNING':return
                trip=await repo.read_trip(run.trip_id)
                if trip.intake.intake_revision!=run.intake_revision or not trip.working or trip.working.revision!=run.base_working_revision or trip.synchronization!='STALE':
                    await repo.finish(run,error=failure('stale_input'));return
                validate_initial(payload,requirements,now=self.now())
                candidate=WorkingView(trip_id=run.trip_id,owner_id=actor,revision=source.revision,requirements=requirements,payload=payload,item_confirmations=(),latest_confirmed_version_id=source.latest_confirmed_version_id,context=dc)
                disclosure,_=preview(trip.model_copy(update={'working':candidate,'synchronization':'CURRENT'}),now=self.now())
                notes=GenerationNotes(transport_advice=[],risks=disclosure.risks)
                dc=DraftContext(**dc.model_dump(exclude={'notes'}),notes=notes)
                v2=ProposalContextV2(schema_version=2,base_intake_revision=run.intake_revision,candidate_requirements=requirements,candidate_input=input,notes=notes,risk_hash='0'*64)
                v2=ProposalContextV2(**v2.model_dump(exclude={'risk_hash'}),risk_hash=risk_hash(v2))
                before={i.id:i for i in source.payload.items};after={i.id:i for i in payload.items}
                touched=sorted(before.keys()|after.keys())
                differences=[]
                for i in touched:
                    old=[c for c in source.payload.costs if c.item_id==i];new=[c for c in payload.costs if c.item_id==i]
                    if before.get(i)!=after.get(i) or old!=new:
                        differences.append(ItemDiff(item_id=i,before=before.get(i),after=after.get(i),before_costs=old,after_costs=new))
                data=dict(proposal_id=str(uuid4()),trip_id=run.trip_id,owner_id=actor,requirements_revision=source.requirements.requirements_revision,
                    output=dict(kind='requirements_replan',base_working_revision=source.revision,explanation='按最新需求生成完整 Mock 候选；授权范围为新旧项目并集，实际差异单独展示。'),candidate=payload.model_dump(mode='json'),level='L3',touched_item_ids=touched)
                p=ReplanProposal.model_validate(data|dict(candidate_hash=candidate_hash(data,v2)))
                context=ProposalContext(source=source.context,candidate_context=dc,differences=differences,source_budget=evaluate_budget(source.payload,source.requirements,{},now=self.now()),candidate_budget=budget)
                await repo.finish_proposal(run,p,context)
                await repo.save_v2_context(p,v2)
        except (OutputFailure,ValidationError):
            await GenerationService(self.uow).fail(run,'invalid_output',actor=actor)
        except ChangeError as error:
            code='known_costs_exceed_hard_limit' if str(error)=='known_costs_exceed_hard_limit' else 'invalid_output'
            diagnostics=budget_data(evaluate_budget(payload,requirements,{},now=self.now())) if code=='known_costs_exceed_hard_limit' else None
            await GenerationService(self.uow).fail(run,code,actor=actor,budget=diagnostics)

    async def decide_in_transaction(self,repo,trip,p,context,run_id,request,*,key,request_hash,operation_id,path,actor):
        v2=await repo.replan_context(p.proposal_id);validate_v2(p,v2)
        if (trip.intake.intake_revision!=request.expected_intake_revision or v2.base_intake_revision!=trip.intake.intake_revision):raise ChangeError('stale_intake')
        if trip.working.revision!=request.expected_working_revision or p.output.base_working_revision!=trip.working.revision:raise ChangeError('stale_revision')
        if trip.synchronization!='STALE' or context.source!=trip.working.context:raise ChangeError('stale_proposal')
        if request.seen_candidate_hash!=p.candidate_hash:raise ChangeError('candidate_mismatch')
        if p.requirements_revision!=trip.working.requirements.requirements_revision:raise ChangeError('proposal_context_mismatch')
        requirements=new_requirements(trip.intake.input,trip.working)
        if (v2.candidate_input!=trip.intake.input or v2.candidate_requirements!=requirements
            or context.candidate_context.input!=v2.candidate_input or context.candidate_context.notes!=v2.notes
            or context.candidate_context.intake_revision!=v2.base_intake_revision):raise ChangeError('proposal_context_mismatch')
        revision=trip.working.revision
        if request.decision=='approve':
            scope=tuple(sorted({i.id for i in trip.working.payload.items}|{i.id for i in p.candidate.items}))
            if scope!=p.touched_item_ids or tuple(sorted(request.item_ids))!=scope:raise ChangeError('authorization_scope_mismatch')
            if request.explicit_authorization is not True:raise ChangeError('specific_authorization_required')
            budget=validate_initial(p.candidate,requirements,now=self.now())
            if any(r.severity=='blocker' for r in v2.notes.risks):raise ChangeError('blocking_risks')
            grant=SpecificAuthorization(authorization_id=str(uuid4()),trip_id=trip.trip_id,proposal_id=p.proposal_id,candidate_hash=p.candidate_hash,base_working_revision=revision,item_ids=scope,granted_by=actor,granted_at=self.now())
            await repo.save_authorization(grant);await repo.consume(grant.authorization_id,operation_id)
            items={i.id:i for i in p.candidate.items}
            remaining=tuple(c for c in trip.working.item_confirmations if c.item_id in items and item_hash(items[c.item_id])==c.item_hash)
            updated=WorkingDraft(trip_id=trip.trip_id,owner_id=actor,revision=revision+1,requirements=requirements,payload=p.candidate,item_confirmations=remaining,latest_confirmed_version_id=trip.latest_confirmed_version_id)
            await repo.advance_replan(trip.working,updated,trip.intake.intake_revision)
            revision=updated.revision
            await repo.attach_change_context(trip.trip_id,revision,context.candidate_context,run_id)
            receipt=dict(operation_id=operation_id,operation_type='apply_working_change',trip_id=trip.trip_id,working_revision=revision,latest_confirmed_version_id=trip.latest_confirmed_version_id,level='L3',budget=budget_data(budget))
            await repo.save_receipt(operation_id,trip.trip_id,'apply_working_change',request_hash,receipt)
        else:
            await repo.save_receipt(operation_id,trip.trip_id,'reject_proposal',request_hash,dict(proposal_id=p.proposal_id,decision='reject'))
        result=DecisionResult(proposal_id=p.proposal_id,decision=request.decision,state='APPLIED' if request.decision=='approve' else 'REJECTED',operation_id=operation_id,working_revision=revision,latest_confirmed_version_id=trip.latest_confirmed_version_id)
        await repo.save_decision(trip.trip_id,result,operation_id,request_hash)
        await repo.save_change_command(key,request_hash,result,trip.trip_id,operation_id,path,200)
        return result
