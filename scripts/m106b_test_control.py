"""Dedicated isolated browser SQL evidence, using the existing single executor."""
import asyncio
import json
import os
import sys
from easytravel.backend.infrastructure.database import connection,load_database_settings
from easytravel.backend.generation_worker import GenerationWorker


async def main(action,trip_id):
    assert os.environ.get('EASYTRAVEL_ISOLATED_TRANSPORT_PORT')=='55434'
    db=load_database_settings()
    async with connection(db) as conn:
        if action=='run':
            assert await GenerationWorker(db).step(conn)
        elif action=='proof':
            tables=['replan_inputs','replan_contexts','generation_runs','change_proposals','proposal_contexts','proposal_decisions','specific_authorizations','working_drafts','draft_versions','draft_contexts','confirmed_snapshots','confirmation_disclosures','api_commands','operation_receipts']
            data={t:await(await conn.execute(f'SELECT * FROM easytravel.{t} WHERE trip_id=%s ORDER BY 1',(trip_id,))).fetchall() for t in tables}
            print(json.dumps(data,ensure_ascii=False,default=str))
        else:raise ValueError('Unknown test action')


if __name__=='__main__':
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform=='win32' else None) as runner:
        runner.run(main(*sys.argv[1:]))
