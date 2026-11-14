"""Compare actual confirmation OpenAPI to the reviewed M1-00 contract."""
import hashlib
import json
from pathlib import Path
from easytravel.backend.main import create_app
from easytravel.backend.config import Settings
from fastapi.openapi.models import OpenAPI

root=Path(__file__).resolve().parents[1]
actual=create_app(Settings(environment='test')).openapi()
OpenAPI.model_validate(actual)
path=root/'Docs/M1-05a_openapi.json'
if path.exists():assert actual==json.loads(path.read_text(encoding='utf-8'))
else:path.write_text(json.dumps(actual,ensure_ascii=False,indent=2),encoding='utf-8')
design=json.loads((root/'Docs/M1-00_API契约草案.md').read_text(encoding='utf-8').split('```json',1)[1].split('```',1)[0])
checks=[]
for endpoint,method,status in [('confirmation-preview','get','200'),('confirmations','post','201')]:
    p='/trips/{trip_id}/'+endpoint
    a=actual['paths']['/api/v1'+p][method];d=design['paths'][p][method]
    assert a['operationId']==d['operationId']
    assert a['responses'][status]['content']['application/json']['schema']==d['responses'][status]['content']['application/json']['schema']
    assert set(a['responses'])==set(d['responses'])
    if method=='post':
        assert a['requestBody']==d['requestBody']
        assert any(x['name']=='Idempotency-Key' and x['required'] and x['schema']['format']=='uuid' for x in a['parameters'])
    checks.append(dict(path='/api/v1'+p,method=method,status=int(status)))
names=['ConfirmRequest','ConfirmResult','ConfirmationPreview','RiskDisclosure','SnapshotView','ConfirmedSnapshot','DraftContext','Risk','BudgetResult','TripView']
for name in names:
    a=actual['components']['schemas'][name];d=design['components']['schemas'][name]
    assert a['additionalProperties'] is False
    assert set(a['required'])==set(d['required'])
    assert set(a['properties'])==set(d['properties'])
assert actual['components']['schemas']['ConfirmRequest']['properties']['explicit_confirmation']['type']=='boolean'
assert actual['components']['schemas']['ConfirmRequest']['properties']['expected_working_revision']['type']=='integer'
for name in ('ConfirmationPreview','SnapshotView'):
    assert actual['components']['schemas'][name]['properties']['booked']['const'] is False
    assert actual['components']['schemas'][name]['properties']['readiness']['const']=='NOT_ASSESSED'
# Existing frontend generated schema is used for WorkingView/GenerationRun, not TripView.
frontend=json.loads((root/'frontend/src/api/generation-schema.json').read_text(encoding='utf-8'))
for name in ('WorkingView','DraftContext','GenerationRun','GenerationNotes','Risk','BudgetResult'):
    assert frontend[name]==actual['components']['schemas'][name]
checksums={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((root/'easytravel/backend/infrastructure/migrations').glob('*.sql'))}
report=dict(result='PASS',endpoints=checks,schemas=names,requiredAndPropertySetsMatch=True,
    runtimeStrictBoolean='false:409; string/number/null:422, covered by PostgreSQL HTTP tests',
    notes=['UnknownCost is a named schema instead of an anonymous inline object.',
           'Pydantic dataclass serialization represents BudgetResult tuples as arrays; runtime tests enforce domain invariants.',
           'Risk item_ids uniqueness follows existing Risk contract; required system risks are additive and canonicalized.'],
    existingFrontendWorkingSchemasUnchanged=True,migrationSHA256=checksums)
(root/'Docs/M1-05a_契约比对.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('Confirmation OpenAPI contract: PASS; 10 schemas, 2 endpoints; migrations hashed')
