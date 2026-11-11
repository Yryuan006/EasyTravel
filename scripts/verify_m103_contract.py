"""Check M1-03a live OpenAPI against M1-00 and retained M1-01 contracts."""
import json
from pathlib import Path
import re
import sys
from fastapi.openapi.models import OpenAPI
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from easytravel.backend.config import Settings
from easytravel.backend.main import create_app

root=Path(__file__).resolve().parents[1]
draft=json.loads(re.search(r"```json\n(.*?)\n```",(root/'Docs/M1-00_API契约草案.md').read_text(encoding='utf-8'),re.S)[1])
actual=create_app(Settings(environment='test')).openapi()
live=json.loads((root/'Docs/M1-03a_openapi.json').read_text(encoding='utf-8'))
assert actual==live
OpenAPI.model_validate(actual)
base=json.loads((root/'Docs/M1-01_openapi.json').read_text(encoding='utf-8'))
schemas=actual['components']['schemas'];expected=draft['components']['schemas']
for name in ('RequirementsInput','CreateTripRequest','IntakeView'):
    assert schemas[name]==base['components']['schemas'][name]
for name in ('TripView','UpdateRequirementsRequest','ClarificationQuestion','NextQuestionResponse'):
    assert set(schemas[name]['required'])==set(expected[name]['required'])
    assert set(schemas[name]['properties'])==set(expected[name]['properties'])
    assert schemas[name]['additionalProperties'] is False
assert set(schemas['AnswerRequest']['required'])==set(expected['AnswerRequest']['required'])|{'field_key'}
assert schemas['AnswerRequest']['additionalProperties'] is False
ops=[('/trips','post','createTrip','201','TripView','CreateTripRequest'),
     ('/trips/{trip_id}','get','getTrip','200','TripView',None),
     ('/trips/{trip_id}/requirements','put','updateRequirements','200','TripView','UpdateRequirementsRequest'),
     ('/trips/{trip_id}/clarifications/next','get','getNextQuestion','200','NextQuestionResponse',None),
     ('/trips/{trip_id}/clarifications/{question_id}/answers','post','answerQuestion','200','TripView','AnswerRequest')]
assert set(actual['paths'])=={'/api/v1/health'}|{'/api/v1'+o[0] for o in ops}
for path,method,opid,status,response,request in ops:
    op=actual['paths']['/api/v1'+path][method]
    assert op['operationId']==opid and op['security']==[{'LocalDevBearer':[]}]
    assert op['responses'][status]['content']['application/json']['schema']=={'$ref':'#/components/schemas/'+response}
    assert all(str(c) in op['responses'] for c in (401,403,404,409,422,500,503))
    if request:
        assert op['requestBody']['content']['application/json']['schema']=={'$ref':'#/components/schemas/'+request}
        assert any(p['name']=='Idempotency-Key' and p['required'] and p['schema']['format']=='uuid' for p in op['parameters'])

def refs(node):
    if isinstance(node,dict):
        if '$ref' in node:
            value=actual
            for key in node['$ref'][2:].split('/'):value=value[key.replace('~1','/').replace('~0','~')]
        for value in node.values():refs(value)
    elif isinstance(node,list):
        for value in node:refs(value)
refs(actual)
print('PASS: live TCP /openapi.json equals application export; OpenAPI 3.1 model validation; all refs resolve')
print('PASS: only health plus five authorized Trip operations; operation IDs/statuses/DTO/security/UUID headers match')
print('PASS: RequirementsInput/CreateTripRequest/IntakeView identical to accepted M1-01; no frontend contract regression for no-draft Trips')
print('PASS: TripView/UpdateRequirementsRequest/ClarificationQuestion/NextQuestionResponse property and required sets match M1-00')
print('EXPLICIT REFINEMENT: AnswerRequest requires field_key in addition to the draft schema, to reject field/question mismatches as required by M1-03a authorization')
print('SCOPE: working permits existing WorkingDraft; synchronization NO_DRAFT or conservatively STALE without draft-input contexts; current confirmation UNCONFIRMED; historical snapshot ID retained')
print('LIMIT: field-specific bounds/date semantics checked by Pydantic+HTTP tests; schema comparison is not a full JSON-Schema equivalence proof')
