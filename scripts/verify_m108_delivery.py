"""Final acceptance evidence checks; no database connection or mutation."""
import hashlib
import json
from pathlib import Path
import subprocess
import struct


def read(name):
    return json.loads(Path('Docs',name).read_text(encoding='utf-8-sig'))


def main():
    for file, summary in [('后端全量回归','454 passed'),('Vitest','151 passed'),('Playwright','61 passed'),('Playwright_生命周期针对性','1 passed')]:
        assert summary in Path('Docs/M1-08_'+file+'.txt').read_text(encoding='utf-8')
    assert read('M1-08_OpenAPI.json')==read('M1-08_运行中OpenAPI.json')
    gates = {}
    for name in ('M1-08_首轮生命周期数据库隔离.json','M1-08_全量数据库隔离.json','M1-08_默认Node全表针对性数据库隔离.json','M1-08_数据库隔离.json'):
        p=read(name)
        assert p['existingExperimentBefore']==p['existingExperimentAfter'] and p['unchanged']
        assert len(p['existingExperimentBefore'])==24 and p['transportHookRemoved']
        assert p['clusterSystemIdentifier']!=p['oldClusterSystemIdentifier'] and p['verifiedDistinctCluster']
        assert all(t['verifiedNonSuperuser'] and t['verifiedEmptyBeforeTests'] for t in p['targets'])
        gates[name]=dict(container=p['isolatedContainer'],cluster=p['clusterSystemIdentifier'],oldCluster=p['oldClusterSystemIdentifier'],old24TablesUnchanged=True,transportHookRemoved=True)
    assert len({p['cluster'] for p in gates.values()})==4
    assert read('M1-08_浏览器工具链.json')['nodeVersion']=='v24.22.2'
    tools=read('M1-08_前端工具链.json')
    assert tools['nodeVersion']=='v24.22.2' and tools['npmVersion']=='11.9.0' and tools['matchesPackageEngines']
    life=read('M1-08_完整生命周期HTTP与PostgreSQL证据.json');proofs=life['proofs']
    assert life['allCommittedBusinessWritesViaBrowser'] and life['noMockRoutes'] and not life['runtimeErrors']
    assert proofs['afterRestart']==proofs['beforeRestart'] and len(proofs['afterRestart'])==21
    assert proofs['clarified']['generation_runs']==[]
    assert life['final']['working']['revision']==5 and life['final']['intake']['intake_revision']==4
    assert life['final']['working']['requirements']['requirements_revision']==2
    assert life['final']['plan_confirmation']=='UNCONFIRMED' and life['final']['latest_confirmed_version_id']==life['s2']['snapshot']['snapshot_id']
    for name,revision in [('s1',1),('ordinary',2),('hotel',3),('rejected',3),('waitingReplan',3),('s2',4),('afterRestart',5)]:
        assert proofs[name]['working_drafts'][0]['revision']==revision
    assert proofs['rejected']['draft_versions']==proofs['hotel']['draft_versions']
    assert proofs['rejected']['specific_authorizations']==proofs['hotel']['specific_authorizations']
    old=proofs['s1']['confirmed_snapshots'][0]
    assert old==next(s for s in proofs['afterRestart']['confirmed_snapshots'] if s['snapshot_id']==old['snapshot_id'])
    assert proofs['s1']['confirmation_disclosures'][0] in proofs['afterRestart']['confirmation_disclosures']
    assert len(proofs['afterRestart']['confirmed_snapshots'])==2
    for view in (life['s1'],life['s2']):
        assert view['booked'] is False and view['readiness']=='NOT_ASSESSED'
        assert view['snapshot']['budget']['verification']!='VERIFIED' and view['snapshot']['budget']['total_cents'] is None
        assert any(c['amount_cents'] is None for c in view['snapshot']['payload']['costs'])
    assert life['s1']['snapshot']['requirements']['start_date']=='2026-12-04'
    assert life['s2']['snapshot']['requirements']['start_date']=='2026-12-11'
    grants=proofs['afterRestart']['specific_authorizations'];assert len(grants)==2
    assert len({g['consumed_by_operation_id'] for g in grants})==2 and all(g['consumed_by_operation_id'] for g in grants)
    for grant in grants:
        p=next(p for p in proofs['afterRestart']['change_proposals'] if p['proposal_id']==grant['proposal_id'])
        assert sorted(grant['item_ids'])==sorted(p['touched_item_ids'])
        d=next(d for d in proofs['afterRestart']['proposal_decisions'] if d['proposal_id']==grant['proposal_id'])
        assert d['operation_id']==grant['consumed_by_operation_id'] and d['decision']=='approve'
    audit=read('M1-08_架构与契约核查.json')
    assert {m['version']:m['checksum'] for m in proofs['afterRestart']['migrations']}==audit['migrations']
    for restart in (life['initialRestart'],life['finalRestart']):
        assert restart['formalCompositionRoot'] and restart['oldPid']!=restart['newPid']
    auth=read('M1-08_全端点身份安全证据.json')
    assert auth['checks']==66 and len({(r['method'],r['path']) for r in auth['records']})==15 and auth['readOnly']
    matrix=read('M1-08_矩阵节点核对.json')
    assert len(matrix['scenarios'])==22 and all(s['status']=='已通过' for s in matrix['scenarios'])
    dimensions=[]
    for p in sorted(Path('Docs').glob('M1-08*.png')):
        header=p.read_bytes()[:24]
        assert header[:8]==b'\x89PNG\r\n\x1a\n' and header[12:16]==b'IHDR'
        width,height=struct.unpack('>II',header[16:24])
        dimensions.append(dict(file=p.name,width=width,height=height))
    assert len(dimensions)==21 and all(p['width']>0 and p['height']>0 for p in dimensions)
    changed=subprocess.run(['git','diff','--name-only'],capture_output=True,text=True,encoding='utf-8',check=True).stdout.splitlines()
    assert all(not p.startswith('easytravel/') and not (p.startswith('Docs/') and not p.startswith('Docs/M1-08')) for p in changed),changed
    def digest(p):
        data=p.read_bytes()
        if p.suffix in {'.json','.txt','.md'}:
            data=data.replace(b'\r\n',b'\n')
        return hashlib.sha256(data).hexdigest()
    hashes={p.name:digest(p) for p in sorted(Path('Docs').glob('M1-08*')) if p.is_file() and p.name!='M1-08_交付核对.json'}
    output=dict(baseline='8889df6651310e9d8f7e56be4e6f751bbf54a4b3',backend=454,vitest=151,playwright=61,
        currentWorkingRevision=5,currentIntakeRevision=4,currentRequirementsRevision=2,snapshots=2,consumedL3Grants=2,
        wholeFormalTableRestartEquality=True,realAPIProcessRestart=True,allBusinessEndpointsSecurityChecks=66,
        oldEvidenceUnchanged=True,migrationsUnchanged=True,gates=gates,screenshots=dimensions,sha256=hashes,hashNormalization='Text CRLF normalized to LF as in Git; PNG exact bytes',stopBeforeM2=True)
    Path('Docs/M1-08_交付核对.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print('All M1-08 results, 21-table lifecycle facts, 22 scenarios and isolation evidence verified')


if __name__=='__main__':main()
