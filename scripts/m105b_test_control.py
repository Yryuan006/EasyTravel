"""Dedicated-test-only fixtures and SQL evidence. No business API or immutable updates."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from easytravel.backend.generation_worker import GenerationWorker
from easytravel.backend.infrastructure.database import load_database_settings, connection
from easytravel.backend.infrastructure.generations import PostgresGenerationRepository
from easytravel.backend.domain.models import WorkingDraft
from easytravel.backend.domain.intake import DraftContext
from scripts.m104b_worker_control import estimated_adapter
from easytravel.backend.infrastructure.mock_planner import mock_adapter

async def main(action,trip_id):
    assert os.environ.get('EASYTRAVEL_ISOLATED_TRANSPORT_PORT')=='55434'
    db=load_database_settings()
    if action in ['rules-v1','rules-v2']:
        Path(os.environ['M105B_RULES_FILE']).write_text('confirmation-v1' if action=='rules-v1' else 'confirmation-v2-test',encoding='utf-8')
        return
    async with connection(db) as conn:
        if action=='proof':
            tables=['confirmed_snapshots','confirmation_disclosures','working_drafts','operation_receipts','api_commands','draft_versions','draft_contexts','specific_authorizations']
            rows={t:await (await conn.execute(f'SELECT * FROM easytravel.{t} WHERE trip_id=%s',(trip_id,))).fetchall() for t in tables}
            print(json.dumps(rows,ensure_ascii=False,default=str));return
        if action=='advance':
            async with conn.transaction():
                await conn.execute('SELECT trip_id FROM easytravel.trips WHERE trip_id=%s FOR UPDATE',(trip_id,))
                await conn.execute('''INSERT INTO easytravel.draft_versions(trip_id,owner_id,revision,requirements_revision,requirements,payload,item_confirmations)
                  SELECT trip_id,owner_id,2,requirements_revision,requirements,payload,item_confirmations FROM easytravel.draft_versions WHERE trip_id=%s AND revision=1''',(trip_id,))
                await conn.execute('UPDATE easytravel.working_drafts SET revision=2 WHERE trip_id=%s AND revision=1',(trip_id,))
            return
        original=PostgresGenerationRepository.attach
        async def attach(repo,draft,context,run):
            if action=='over':
                data=draft.model_dump(mode='json');data['requirements']['hard_limit_cents']=1
                draft=WorkingDraft.model_validate(data)
            if action=='blocker':
                data=context.model_dump(mode='json');data['notes']['risks'].append(dict(code='route_blocked',severity='blocker',message='路线存在必须解决的阻塞风险。',item_ids=[]))
                context=DraftContext.model_validate(data)
            await original(repo,draft,context,run)
        with patch.object(PostgresGenerationRepository,'attach',attach):
            assert await GenerationWorker(db,adapter_factory=estimated_adapter if action=='estimated' else mock_adapter).step(conn)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['run','estimated','over','blocker','advance','proof','rules-v1','rules-v2']);p.add_argument('trip_id',nargs='?',default='')
    a=p.parse_args()
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform=='win32' else None) as r:r.run(main(a.action,a.trip_id))
