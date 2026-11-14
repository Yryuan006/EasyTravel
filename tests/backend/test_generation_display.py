"""M1-04b read projection and retry classification gates using the generation test DB."""
from test_generations import db, services, app, http, create, accept, step, run, headers, REQUEST, ACTOR_A, TOKEN_B
import json
import pytest
from easytravel.backend.application.generations import failure
from easytravel.backend.infrastructure.mock_planner import mock_adapter
from easytravel.backend.infrastructure.generations import unit_of_work
from easytravel.model_adapter import MockAdapter, ModelReply

@pytest.mark.parametrize('code,allowed', [('known_costs_exceed_hard_limit',False),('stale_input',False),('invalid_output',True),('worker_interrupted',True),('persistence_failed',True)])
def test_retry_classification(code,allowed):
    assert failure(code)['retryable'] is allowed

def test_projection_reopen_owner_revision(http,db):
    path=create(http)
    assert http.get(path+'/generation-state',headers=headers()).json()['source_run'] is None
    accepted=accept(http,path);run(step(db))
    state=http.get(path+'/generation-state',headers=headers())
    assert state.status_code==200 and state.headers['cache-control']=='no-store'
    state=state.json()
    source=http.get(path+'/generations/'+accepted['run_id'],headers=headers()).json()
    assert state['source_run']==state['latest_run']==source
    assert state['working_revision']==source['result']['working_revision']==1
    assert state['source_run']['diagnostics_budget']['total_cents'] is None
    assert http.get(path+'/generation-state',headers=headers()).json()==state
    assert http.get(path+'/generation-state',headers=headers(token=TOKEN_B)).status_code==404
    other=create(http)
    assert http.get(other+'/generation-state',headers=headers()).json()['source_run'] is None
    trip=http.get(path,headers=headers()).json()
    updated=http.put(path+'/requirements',headers=headers(),json=dict(expected_intake_revision=1,expected_working_revision=1,requirements=trip['intake']['input']))
    assert updated.status_code==200
    assert http.get(path+'/generation-state',headers=headers()).json()['source_run']==source
    assert http.get(path,headers=headers()).json()['synchronization']=='STALE'

def test_over_limit_blocks_retry_without_changing_receipt(http,db,services):
    path=create(http);accepted=accept(http,path)
    t=run(services[0].get(accepted['trip_id'],actor=ACTOR_A))
    payload=json.loads(mock_adapter(t.intake.input).responses[0].text)
    payload['costs'][-1]['amount_cents']=999999
    run(step(db,lambda _:MockAdapter(ModelReply(json.dumps(payload)))))
    failed=http.get(path+'/generations/'+accepted['run_id'],headers=headers()).json()
    assert failed['error']['retryable'] is False
    response=http.post(path+'/generations',headers=headers(),json=REQUEST|dict(retry_of=accepted['run_id']))
    assert response.status_code==409 and response.json()['error']['code']=='invalid_retry'
    assert http.get(path,headers=headers()).json()['working'] is None
    assert http.get(path+'/generation-state',headers=headers()).json()['latest_run']==failed
