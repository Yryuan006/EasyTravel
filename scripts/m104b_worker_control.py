"""Offline browser-test worker control. No test-only HTTP routes or plan submission."""
import argparse, asyncio, sys, json
from easytravel.backend.generation_worker import GenerationWorker
from easytravel.backend.infrastructure.database import load_database_settings, connection
from easytravel.backend.infrastructure.generations import PostgresGenerationRepository
from easytravel.backend.infrastructure.mock_planner import mock_adapter
from easytravel.model_adapter import MockAdapter, ModelReply

def estimated_adapter(input):
    payload=json.loads(mock_adapter(input).responses[0].text)
    for n,cost in enumerate(payload['costs']):
        cost.update(amount_cents=0 if n==0 else (cost['amount_cents'] if cost['amount_cents'] is not None else 1000),category='estimate',unknown_reason=None)
    return MockAdapter(ModelReply(json.dumps(payload)))

async def main(action):
    db=load_database_settings()
    worker=GenerationWorker(db,adapter_factory=(lambda _:MockAdapter(ModelReply('INVALID PRIVATE MODEL OUTPUT'),ModelReply('INVALID PRIVATE MODEL OUTPUT'))) if action=='invalid' else estimated_adapter if action=='estimated' else mock_adapter)
    async with connection(db) as conn:
        if action=='claim':
            assert await worker.claim(conn)
        elif action=='recover':
            assert await worker.recover(conn)
        else:
            rows=await (await conn.execute("SELECT run_id,trip_id,owner_id FROM easytravel.generation_runs WHERE status='RUNNING' ORDER BY created_at LIMIT 1")).fetchall()
            if rows:
                row=rows[0]
                record=await PostgresGenerationRepository(conn,row['owner_id']).run(row['trip_id'],row['run_id'])
                await worker.service.execute(record,actor=row['owner_id'],adapter_factory=worker.adapter_factory)
            else:
                assert await worker.step(conn)
    print('Worker action completed:',action)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['claim','run','invalid','recover','estimated'])
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform=='win32' else None) as runner:
        runner.run(main(parser.parse_args().action))
