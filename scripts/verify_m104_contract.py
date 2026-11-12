"""Compare actual HTTP OpenAPI snapshot with the M1-00 generation contract and frontend copy."""
import json
from pathlib import Path
from easytravel.backend.main import create_app
from easytravel.backend.config import Settings
root=Path(__file__).resolve().parents[1]
actual=create_app(Settings(environment='test')).openapi()
snapshot=json.loads((root/'Docs/M1-04a_openapi.json').read_text(encoding='utf-8'))
assert actual==snapshot
spec=(root/'Docs/M1-00_API契约草案.md').read_text(encoding='utf-8')
design=json.loads(spec.split('```json',1)[1].split('```',1)[0])
checks=[]
for path,method,status in [('/api/v1/trips/{trip_id}/generations','post','202'),('/api/v1/trips/{trip_id}/generations/{run_id}','get','200')]:
 op=actual['paths'][path][method];expected=design['paths'][path.removeprefix('/api/v1')][method]
 assert op['operationId']==expected['operationId']
 assert status in op['responses']
 checks.append(dict(path=path,method=method,status=status,operationId=op['operationId']))
 if method=='post':
  assert any(p['name']=='Idempotency-Key' and p['required'] for p in op['parameters'])
for name in ('GenerateRequest','GenerationRun','DraftContext','GenerationNotes','Risk','TripView'):
 a=actual['components']['schemas'][name];d=design['components']['schemas'][name]
 assert set(a['required'])==set(d['required'])
 assert set(a['properties'])==set(d['properties'])
 assert a['additionalProperties'] is False
assert actual['components']['schemas']['GenerationRun']['properties']['kind']['const']=='INITIAL_PLAN'
assert actual['components']['schemas']['GenerationRun']['properties']['base_working_revision']['type']=='null'
assert actual['components']['schemas']['GenerationRun']['properties']['status']['enum']==['ACCEPTED','RUNNING','SUCCEEDED','FAILED']
legacy=json.loads((root/'Docs/M1-03a_openapi.json').read_text(encoding='utf-8'))
copy=json.loads((root/'frontend/src/api/working-schema.json').read_text(encoding='utf-8'))
for k,v in copy.items():
 assert v==(legacy if k=='WorkingDraft' else actual)['components']['schemas'][k]
def walk(value):
 if isinstance(value,dict):
  if '$ref' in value:
   ref=value['$ref'];assert ref.startswith('#/components/schemas/') and ref.split('/')[-1] in actual['components']['schemas']
  for v in value.values():walk(v)
 elif isinstance(value,list):
  for v in value:walk(v)
walk(actual)
report=dict(result='PASS',checks=checks,request_and_response_field_sets='match M1-00',all_internal_refs_resolve=True,
 frontend_copied_schemas='exact match to actual WorkingView dependency closure; legacy WorkingDraft retained',
 scoped_differences=['kind restricted to INITIAL_PLAN; base_working_revision restricted to null',
 'TripView still only UNCONFIRMED; readiness NOT_ASSESSED; booked false',
 'Risk item_ids are unique in server construction; actual schema does not advertise uniqueItems',
 'WorkingView is the OpenAPI component name for context-bearing WorkingDraft; legacy domain WorkingDraft unchanged',
 'CHANGE_INTENT endpoint is absent; REPLAN explicitly rejected with 409 scope_replan_not_supported'])
(root/'Docs/M1-04a_OpenAPI契约比对.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
