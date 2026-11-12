"""Test-only process barrier around the production executor; no production hook or SQL shortcut."""
import asyncio
import os
from pathlib import Path
from easytravel.backend.generation_worker import GenerationWorker
from easytravel.backend.infrastructure.database import load_database_settings, connection
from easytravel.backend.infrastructure.mock_planner import mock_adapter

async def main():
    directory=Path(os.environ['PROCESS_BARRIER'])
    mode=os.environ['PROCESS_MODE']
    class Blocked:
        async def generate(self,*,repair):
            (directory/'running').touch()
            await asyncio.sleep(60)
    worker=GenerationWorker(load_database_settings(),adapter_factory=(lambda _:Blocked()) if mode=='running' else mock_adapter)
    async with connection(worker.settings) as conn:
        locked=await (await conn.execute('SELECT pg_try_advisory_lock(1040104) AS locked')).fetchone()
        if not locked['locked']:
            raise RuntimeError('generation_executor_already_running')
        await worker.recover(conn)
        (directory/'ready').touch()
        while not (directory/'claim').exists():
            await asyncio.sleep(.01)
        await worker.step(conn)
        (directory/'committed').touch()
        os._exit(0)

with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
    runner.run(main())
