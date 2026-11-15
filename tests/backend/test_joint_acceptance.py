"""All formal business endpoints share the same identity and no-leak boundary."""
import json
from pathlib import Path
from test_snapshots import app, db, replan_app, change_app, services, generation_app, http, fingerprint
from test_confirmations import generated, preview, request as confirm_request, confirm
from test_change_workflow import proposal, decision
from test_generations import run, headers, TOKEN_B
from test_intakes import TOKEN_A, input_data


def test_all_business_routes_authority_and_safe_errors(http, db):
    path = generated(http, db)
    p = preview(http, path)
    result = confirm(http, path, confirm_request(p)).json()
    pp, proposed = proposal(http, db, path)
    tid = path.split('/')[-1]
    sid = result['snapshot']['snapshot']['snapshot_id']
    async def run_id():
        from easytravel.backend.infrastructure.database import connection
        async with connection(db) as conn:
            return (await (await conn.execute('SELECT run_id FROM easytravel.generation_runs WHERE trip_id=%s ORDER BY created_at DESC LIMIT 1', (tid,))).fetchone())['run_id']
    rid = run(run_id())
    routes = [
        ('POST','/api/v1/trips', input_data(True)),
        ('GET',path,None),
        ('PUT',path+'/requirements',dict(expected_intake_revision=1,expected_working_revision=1,requirements=input_data(True)['requirements'])),
        ('GET',path+'/clarifications/next',None),
        ('POST',path+'/clarifications/missing/answers',dict(expected_intake_revision=1,expected_working_revision=1,field_key='travelers',answer=dict(state='provided',value=2))),
        ('POST',path+'/generations',dict(expected_intake_revision=1,expected_working_revision=1,retry_of=None)),
        ('GET',path+'/generations/'+rid,None),
        ('GET',path+'/generation-state',None),
        ('GET',path+'/confirmation-preview',None),
        ('POST',path+'/confirmations',confirm_request(p)),
        ('POST',path+'/change-intents',dict(expected_intake_revision=1,expected_working_revision=1,intent='第二天安排轻松一点',retry_of=None)),
        ('GET',pp,None),
        ('POST',pp+'/decisions',decision(proposed)),
        ('GET',path+'/snapshots',None),
        ('GET',path+'/snapshots/'+sid,None),
    ]
    before = run(fingerprint(db))
    records = []
    for method, url, body in routes:
        def send(h, data=body, query=None):
            return http.request(method,url,headers=h,**({'json':data} if data is not None else {}),**({'params':query} if query else {}))
        cases = [('missing_token',send({}),401), ('forged_actor_header',send(headers()|{'X-Actor':'forged'}),422)]
        if url != '/api/v1/trips':
            cases.append(('other_owner',send(headers(token=TOKEN_B)),404))
        if method == 'GET':
            cases.append(('forged_owner_query',send(headers(),query={'owner_id':'forged'}),422))
        else:
            cases += [('forged_owner_body',send(headers(),body|dict(owner_id='forged')),422),
                      ('model_payload_body',send(headers(),body|dict(candidate=proposed['proposal']['candidate'])),422)]
        for name, response, expected in cases:
            assert response.status_code == expected,(method,url,name,response.text)
            assert set(response.json()) == {'error'}
            assert set(response.json()['error']) == {'code','message'}
            assert all(secret not in response.text for secret in (TOKEN_A,TOKEN_B,'postgresql://','SELECT ','Traceback','password='))
            records.append(dict(method=method,path=url,case=name,status=expected,body=response.json()))
    assert run(fingerprint(db)) == before
    Path('Docs/M1-08_全端点身份安全证据.json').write_text(json.dumps(dict(records=records,checks=len(records),readOnly=True),ensure_ascii=False,indent=2),encoding='utf-8')
