"""Explicit single executor: python -m easytravel.backend.generation_worker.

Startup fails all leftover ACCEPTED/RUNNING runs. New-key retry_of is required.
A session advisory lock excludes another executor, and releases on process death.
No migration, HTTP GET execution, in-memory queue or checkpoint dependency.
"""
import argparse
import asyncio
import sys
from psycopg.types.json import Jsonb
from .infrastructure.database import connection, load_database_settings
from .infrastructure.generations import unit_of_work, PostgresGenerationRepository
from .infrastructure.mock_planner import mock_adapter
from .application.generations import GenerationService, failure

class GenerationWorker:
    def __init__(self, settings, *, adapter_factory=mock_adapter):
        self.settings, self.adapter_factory = settings, adapter_factory
        self.service = GenerationService(unit_of_work(settings))

    async def recover(self, conn):
        async with conn.transaction():
            rows = await (await conn.execute("""UPDATE easytravel.generation_runs SET status='FAILED',error=%s,updated_at=clock_timestamp()
                WHERE status IN ('ACCEPTED','RUNNING') RETURNING run_id""", (Jsonb(failure('worker_interrupted')),))).fetchall()
            return [r['run_id'] for r in rows]

    async def claim(self, conn):
        async with conn.transaction():
            row = await (await conn.execute("""SELECT run_id,trip_id,owner_id FROM easytravel.generation_runs
                WHERE status='ACCEPTED' ORDER BY created_at,run_id FOR UPDATE SKIP LOCKED LIMIT 1""")).fetchone()
            if row is None:
                return None
            await conn.execute("UPDATE easytravel.generation_runs SET status='RUNNING',updated_at=clock_timestamp() WHERE run_id=%s", (row['run_id'],))
            run = await PostgresGenerationRepository(conn,row['owner_id']).run(row['trip_id'],row['run_id'])
            return run,row['owner_id']

    async def step(self, conn):
        claimed = await self.claim(conn)
        if claimed is None:
            return False
        run,actor = claimed
        service = self.service
        adapter_factory = self.adapter_factory
        if run.kind == 'CHANGE_INTENT':
            from .application.changes import ChangeService
            from .infrastructure.changes import unit_of_work as change_uow
            from .infrastructure.mock_changes import mock_change_adapter
            service = ChangeService(change_uow(self.settings))
            adapter_factory = mock_change_adapter
        try:
            await service.execute(run,actor=actor,adapter_factory=adapter_factory)
        except asyncio.CancelledError:
            await self.service.fail(run,'worker_interrupted',actor=actor)
            raise
        except Exception:
            # The failed business transaction has exited/rolled back before this separate write.
            # If DB itself is unavailable, exit and let next startup recovery persist the failure.
            await self.service.fail(run,'persistence_failed',actor=actor)
        return True

    async def serve(self, *, once=False):
        async with connection(self.settings) as conn:
            locked = await (await conn.execute('SELECT pg_try_advisory_lock(1040104) AS locked')).fetchone()
            if not locked['locked']:
                raise RuntimeError('generation_executor_already_running')
            recovered = await self.recover(conn)
            print(f'Generation executor ready; recovered={len(recovered)}',flush=True)
            while True:
                await self.step(conn)
                if once:
                    return
                await asyncio.sleep(0.1)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once',action='store_true')
    args = parser.parse_args()
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform=='win32' else None) as runner:
        runner.run(GenerationWorker(load_database_settings()).serve(once=args.once))

if __name__ == '__main__':
    main()
