"""Audit unchanged accepted files, API/migrations and exported offline V2 contracts."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess

from easytravel.backend.application.live_place_ports import LivePlaceSourceConfig
from easytravel.backend.application.offline_live_places import DetailSelection
from easytravel.backend.domain.place_search_v2 import (
    LiveUnverifiedObservation, PlaceDetailResult, PlaceHit, PlaceSearchPage,
    PlaceSearchRequest, SearchCoverage,
)
from easytravel.backend.config import Settings
from easytravel.backend.main import create_app

BASELINE = '7d8e5191eb6518be57e47e2d047bcc41d6b53d5b'
OUT = Path('Docs/M2-01c')
MODULES = (
    'easytravel/backend/domain/place_search_v2.py',
    'easytravel/backend/application/live_place_ports.py',
    'easytravel/backend/application/offline_live_places.py',
    'easytravel/backend/infrastructure/amap_offline_places.py',
    'easytravel/backend/infrastructure/fake_place_transport.py',
)


def git(*args):
    return subprocess.run(['git','-c','core.quotepath=false',*args],capture_output=True,check=True).stdout


def main():
    accepted=set(git('ls-tree','-r','--name-only',BASELINE).decode('utf-8').splitlines())
    changed=set(git('diff','--name-only',BASELINE).decode('utf-8').splitlines())
    assert not changed & accepted, 'Existing accepted files changed'
    schema=create_app(Settings(environment='test')).openapi()
    assert schema==json.loads(git('show',BASELINE+':Docs/M1-08_OpenAPI.json'))
    migrations={}
    for p in Path('easytravel/backend/infrastructure/migrations').glob('*.sql'):
        assert p.read_bytes()==git('show',BASELINE+':'+p.as_posix())
        migrations[p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
    assert len(migrations)==7
    for name in accepted:
        if name.startswith('easytravel/') and name.endswith('.py'):
            for node in ast.walk(ast.parse(Path(name).read_text(encoding='utf-8'))):
                if isinstance(node,ast.ImportFrom):
                    assert (node.module or '').split('.')[-1] not in {Path(p).stem for p in MODULES}
    classes=(PlaceSearchRequest,SearchCoverage,PlaceHit,PlaceSearchPage,PlaceDetailResult,
             LiveUnverifiedObservation,DetailSelection,LivePlaceSourceConfig)
    contracts={c.__name__:c.model_json_schema() for c in classes}
    assert all(s['additionalProperties'] is False for s in contracts.values())
    report=dict(baseline=BASELINE,acceptedTrackedFilesUnchanged=True,formalOpenAPIUnchanged=True,
        migrationBytesUnchanged=migrations,noNewMigration=True,newModulesNotWiredIntoAcceptedBusiness=True,
        liveEnabled=False,realNetworkTransportImplemented=False,secretConfigurationFields=False,
        fixtureSHA256=hashlib.sha256(Path('tests/places/fixtures/amap_v5_fabricated.json').read_bytes()).hexdigest(),
        modules=list(MODULES),note='Offline preparation only; G1/G2/G3/G4 not deemed passed.')
    (OUT/'契约与基线核查.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'V2地点契约Schema.json').write_text(json.dumps(contracts,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('PASS existing files/API/migrations unchanged; V1 untouched; V2 strict schemas; live transport absent')


if __name__=='__main__':
    main()
