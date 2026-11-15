"""Accept, validate and attach a first mock draft to an existing owned Trip."""
from datetime import datetime, timezone
from uuid import uuid4
from ...model_adapter import generate_output, OutputFailure
from ...contracts import PlanDraftOutput
from ...rules import evaluate_budget
from ..domain.hashing import digest
from ..domain.rules import ChangeError
from ..domain.generation import requirements_from, validate_initial
from ..domain.intake import DraftContext, GenerationNotes, Risk
from ..domain.models import WorkingDraft
from .plans import PlanService, budget_data

ERROR_MESSAGES = {
    "unsupported_change_intent": "Mock supports only the documented exact change commands; rewrite the intent.",
    "invalid_output": "Mock output failed validation.",
    "known_costs_exceed_hard_limit": "Known costs exceed the hard budget limit.",
    "stale_input": "Requirements changed during generation.",
    "worker_interrupted": "Executor stopped before completing this run; explicitly retry with a new key.",
    "persistence_failed": "Draft transaction failed; no partial draft was committed.",
}

def failure(code):
    return dict(code=code,message=ERROR_MESSAGES[code],retryable=code not in ("known_costs_exceed_hard_limit", "stale_input"))

class GenerationService:
    def __init__(self, uow, *, now=lambda: datetime.now(timezone.utc)):
        self.uow, self.now = uow, now

    async def accept(self, trip_id, request, *, key, actor):
        PlanService.identifier(actor)
        path = f"/api/v1/trips/{trip_id}/generations"
        request_hash = digest(request.model_dump(mode='json'))
        async with self.uow(actor) as repo:
            await repo.lock_command(digest(dict(actor=actor,method='POST',path=path,key=key)))
            replay = await repo.replay(key,request_hash,path)
            if replay is not None:
                return replay
            await repo.lock_trip(trip_id)
            trip = await repo.read_trip(trip_id)
            if trip.intake.intake_revision != request.expected_intake_revision:
                raise ChangeError('stale_intake')
            if trip.intake.blocking_fields:
                raise ChangeError('requirements_incomplete')
            if trip.active_run_id:
                raise ChangeError('generation_in_progress')
            kind='REPLAN' if trip.working else 'INITIAL_PLAN'
            if trip.working:
                if trip.synchronization!='STALE' or trip.working.context is None:
                    raise ChangeError('scope_replan_not_supported')
                if request.expected_working_revision!=trip.working.revision:
                    raise ChangeError('stale_revision')
            elif request.expected_working_revision is not None:
                raise ChangeError('stale_revision')
            from .retries import retry_attempt
            attempt=await retry_attempt(repo,trip_id,request,kind)
            if kind=='REPLAN':
                result=await repo.accept_replan(trip,str(uuid4()),attempt,request.retry_of)
                await repo.save_command(key,request_hash,result,str(uuid4()),str(uuid4()),'POST',path,202)
                return result
            result = await repo.accept(trip,str(uuid4()),attempt,request.retry_of)
            await repo.save_command(key,request_hash,result,str(uuid4()),str(uuid4()),'POST',path,202)
            return result

    async def get(self, trip_id, run_id, *, actor):
        PlanService.identifier(actor)
        async with self.uow(actor) as repo:
            return await repo.run(trip_id,run_id)

    async def state(self, trip_id, *, actor):
        PlanService.identifier(actor)
        async with self.uow(actor) as repo:
            await repo.lock_trip(trip_id)
            return await repo.state(trip_id)

    async def frozen_input(self, run, *, actor):
        async with self.uow(actor) as repo:
            # Read the immutable version, never the browser or latest mutable pointer.
            return await repo.frozen_input(run)

    async def attach_initial_draft(self, run, payload, input, *, actor):
        requirements = requirements_from(input)
        budget = validate_initial(payload,requirements,now=self.now())
        notes = GenerationNotes(transport_advice=[],risks=[Risk(code='mock_unverified',severity='warning',
            message='来源：可控 Mock。路线、营业、房型建议与所有价格未经核验；无真实班次或预订。',item_ids=[i.id for i in payload.items])])
        if budget.unknown_cost_ids:
            notes.risks.append(Risk(code='unknown_costs',severity='warning',message='存在未知费用，总额无法确定，不代表免费。',item_ids=sorted({c.item_id for c in payload.costs if c.amount_cents is None})))
        context = DraftContext(intake_revision=run.intake_revision,input=input,notes=notes,risk_rules_version='mock-initial-v1')
        async with self.uow(actor) as repo:
            await repo.lock_trip(run.trip_id)
            current = await repo.run(run.trip_id,run.run_id,lock=True)
            if current.status != 'RUNNING':
                return
            trip = await repo.read_trip(run.trip_id)
            if trip.intake.intake_revision != run.intake_revision or trip.working is not None:
                await repo.finish(run,error=failure('stale_input'))
                return
            draft = WorkingDraft(trip_id=run.trip_id,owner_id=actor,revision=1,requirements=requirements,
                payload=payload,item_confirmations=(),latest_confirmed_version_id=None)
            await repo.attach(draft,context,run)
            await repo.finish(run,budget=budget_data(budget))

    async def fail(self, run, code, *, actor, budget=None):
        async with self.uow(actor) as repo:
            await repo.lock_trip(run.trip_id)
            current = await repo.run(run.trip_id,run.run_id,lock=True)
            if current.status == 'RUNNING':
                await repo.finish(run,error=failure(code),budget=budget)

    async def execute(self, run, *, actor, adapter_factory):
        input = await self.frozen_input(run,actor=actor)
        try:
            payload = await generate_output(adapter_factory(input),PlanDraftOutput)
            await self.attach_initial_draft(run,payload,input,actor=actor)
        except OutputFailure:
            await self.fail(run,'invalid_output',actor=actor)
        except ChangeError as error:
            code = 'known_costs_exceed_hard_limit' if str(error) == 'known_costs_exceed_hard_limit' else 'invalid_output'
            budget = budget_data(evaluate_budget(payload,requirements_from(input),{},now=self.now())) if code == 'known_costs_exceed_hard_limit' else None
            await self.fail(run,code,actor=actor,budget=budget)
