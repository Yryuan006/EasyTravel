"""Isolated acceptance driver: one formal Mock worker step, read-only facts, API restart."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4
from easytravel.backend.generation_worker import GenerationWorker
from easytravel.backend.infrastructure.database import connection, load_database_settings


async def main(action, trip_id):
    assert os.environ.get('EASYTRAVEL_ISOLATED_TRANSPORT_PORT') == '55434'
    if action == 'restart':
        request_id = uuid4().hex
        request = Path(os.environ['EASYTRAVEL_M108_RESTART_REQUEST'])
        ack = Path(os.environ['EASYTRAVEL_M108_RESTART_ACK'])
        request.write_text(json.dumps(dict(request_id=request_id)), encoding='utf-8')
        for _ in range(200):
            if ack.exists():
                result = json.loads(ack.read_text(encoding='utf-8'))
                if result['request_id'] == request_id:
                    assert result['formalCompositionRoot'] and result['oldPid'] != result['newPid']
                    print(json.dumps(result))
                    return
            await asyncio.sleep(.1)
        raise RuntimeError('Formal API restart did not complete')
    db = load_database_settings()
    async with connection(db) as conn:
        if action == 'run':
            assert await GenerationWorker(db).step(conn)
        elif action == 'proof':
            await conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            tables = ['trips', 'trip_intakes', 'trip_intake_versions', 'clarification_questions', 'clarification_answers',
                'replan_inputs', 'replan_contexts', 'generation_runs', 'change_proposals',
                'change_run_inputs', 'proposal_contexts', 'proposal_decisions', 'specific_authorizations', 'working_drafts',
                'draft_versions', 'draft_contexts', 'confirmed_snapshots', 'confirmation_disclosures',
                'api_commands', 'operation_receipts']
            order = {'trip_intake_versions':'revision','draft_versions':'revision','draft_contexts':'working_revision'}
            rows = {t: await (await conn.execute(f'SELECT * FROM easytravel.{t} WHERE trip_id=%s ORDER BY {order.get(t, "1")}', (trip_id,))).fetchall() for t in tables}
            rows['migrations'] = await (await conn.execute('SELECT * FROM easytravel.schema_migrations ORDER BY 1')).fetchall()
            print(json.dumps(rows, ensure_ascii=False, default=str))
        else:
            raise ValueError('Unsupported test driver action')


if __name__ == '__main__':
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == 'win32' else None) as runner:
        runner.run(main(*sys.argv[1:]))
