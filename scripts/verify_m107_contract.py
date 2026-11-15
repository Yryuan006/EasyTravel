"""Compare M1-00, actual OpenAPI, TS DTO fields, and immutable baseline sources."""
import hashlib
import json
import re
import subprocess
from pathlib import Path
from easytravel.backend.main import create_app
from easytravel.backend.config import Settings

actual = create_app(Settings(environment='test')).openapi()
schemas = actual['components']['schemas']
Path('Docs/M1-07_OpenAPI.json').write_text(json.dumps(actual, ensure_ascii=False, indent=2), encoding='utf-8')
runtime = json.loads(Path('frontend/src/api/snapshot-schema.json').read_text(encoding='utf-8'))
assert runtime == {name: schemas[name] for name in ('SnapshotPage', 'SnapshotSummary')}
available = {}
for name in ('working', 'generation', 'confirmation', 'change', 'snapshot'):
    available.update(json.loads(Path('frontend/src/api/'+name+'-schema.json').read_text(encoding='utf-8')))
seen = set()
def visit(name):
    if name in seen:
        return
    seen.add(name)
    assert available[name] == schemas[name], name
    def walk(node):
        if isinstance(node, dict):
            if '$ref' in node:
                visit(node['$ref'].split('/')[-1])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(schemas[name])
visit('SnapshotView')
visit('SnapshotPage')
text = Path('Docs/M1-00_API契约草案.md').read_text(encoding='utf-8')
expected = json.loads(re.search(r'```json\s*(\{.*?\})\s*```', text, re.S).group(1))
checks = {}
for name in ('SnapshotPage', 'SnapshotView'):
    a, e = schemas[name], expected['components']['schemas'][name]
    assert set(a['properties']) == set(e['properties']) and set(a['required']) == set(e['required'])
    assert a['additionalProperties'] == e['additionalProperties'] == False
    checks[name] = dict(properties=sorted(a['properties']), required=sorted(a['required']), matchesM100=True)
a = schemas['SnapshotSummary']
e = expected['components']['schemas']['SnapshotPage']['properties']['items']['items']
assert set(a['properties']) == set(e['properties']) and set(a['required']) == set(e['required'])
checks['SnapshotSummary'] = dict(matchesM100InlineItem=True, properties=list(a['properties']))
for name in ['SnapshotSummary', 'SnapshotPage']:
    ts = Path('frontend/src/api/snapshots.ts').read_text(encoding='utf-8')
    fields = re.search(r'export interface '+name+r' \{([^}]+)\}', ts).group(1)
    assert set(re.findall(r'(\w+)\s*:', fields)) == set(schemas[name]['properties'])
checks['TypeScript'] = dict(summaryAndPageFieldsMatch=True, SnapshotViewReusesConfirmationContract=True, runtimeSchemaEqualsActualOpenAPI=True)
checks['TypeScript']['reachableRuntimeSchemasVerified'] = sorted(seen)
checks['paginationParameters'] = actual['paths']['/api/v1/trips/{trip_id}/snapshots']['get']['parameters']
checks['migrations'] = {}
baseline = '58c520232239c637a4d93a33ad99487e653b35d1'
for p in sorted(Path('easytravel/backend/infrastructure/migrations').glob('*.sql')):
    old = subprocess.run(['git', 'show', baseline+':'+p.as_posix()], capture_output=True, check=True).stdout
    assert old == p.read_bytes()
    checks['migrations'][p.name] = hashlib.sha256(old).hexdigest()
for name in ['easytravel/backend/application/confirmations.py', 'easytravel/backend/infrastructure/confirmations.py', 'easytravel/backend/infrastructure/repository.py']:
    old = subprocess.run(['git', 'show', baseline+':'+name], capture_output=True, check=True).stdout
    assert old.decode().replace('\r\n', '\n') == Path(name).read_text(encoding='utf-8')
checks['confirmationAndLegacyRepositoryUnchangedIgnoringCheckoutNewlines'] = True
Path('Docs/M1-07_契约与迁移核对.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
print('M1-00, actual OpenAPI, TS fields/runtime schema and migrations 001-007 verified')
