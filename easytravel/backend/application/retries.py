"""One explicit failed-run retry policy for every generation kind."""
from ..domain.rules import ChangeError


async def retry_attempt(repo,trip_id,request,kind):
    base=request.expected_working_revision
    if request.retry_of is None:
        if await repo.failed_for_source(trip_id,request.expected_intake_revision,base,kind):
            raise ChangeError('explicit_retry_required')
        return 1
    old=await repo.run(trip_id,request.retry_of)
    if (old.kind!=kind or old.status!='FAILED' or old.intake_revision!=request.expected_intake_revision
            or old.base_working_revision!=base or not old.error or not old.error.retryable):
        raise ChangeError('invalid_retry')
    return old.attempt+1
