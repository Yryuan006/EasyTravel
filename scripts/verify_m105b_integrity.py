"""Read-only final evidence checks; never connects to a business database."""
import hashlib
import json
from pathlib import Path
import subprocess
from psycopg.conninfo import conninfo_to_dict

BASELINE='7149f735dc09e4b10165208efb850b66841e4240'
root=Path(__file__).resolve().parents[1]

def main():
    def git(*args):
        return subprocess.run(['git',*args],cwd=root,capture_output=True,check=True).stdout
    assert not git('diff',BASELINE,'--','easytravel','pyproject.toml','uv.lock')
    assert not git('diff',BASELINE,'--','Docs',':(exclude)Docs/M1-05b*')
    migrations={}
    for path in (root/'easytravel/backend/infrastructure/migrations').glob('*.sql'):
        relative=path.relative_to(root).as_posix()
        assert git('show',BASELINE+':'+relative)==path.read_bytes()
        migrations[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
    isolation=[]
    for name in ['M1-05b_后端与首轮浏览器隔离.json','M1-05b_二轮浏览器隔离.json','M1-05b_数据库隔离.json']:
        data=json.loads((root/'Docs'/name).read_text(encoding='utf-8'))
        assert data['unchanged'] and data['transportHookRemoved'] and data['existingENV00Uninterrupted']
        assert data['existingExperimentBefore']==data['existingExperimentAfter']
        assert all(t['verifiedEmptyBeforeTests'] for t in data['targets'])
        isolation.append(data['isolatedContainer'])
    assert not (root/'.venv/Lib/site-packages/easytravel_m105b_test_transport.pth').exists()
    for file,expected in [('M1-05b_后端全量回归.txt','384 passed'),('M1-05b_后端针对性确认测试.txt','26 passed'),('M1-05b_Playwright.txt','53 passed'),('M1-05b_Vitest.txt','115 passed')]:
        assert expected in (root/'Docs'/file).read_text(encoding='utf-8')
    records=json.loads((root/'Docs/M1-05b_HTTP与数据库证据.json').read_text(encoding='utf-8'))['records']
    successes=0
    for case in records:
        if 'proof' not in case:continue
        proof=case['proof']
        assert not proof['specific_authorizations']
        if proof['confirmed_snapshots']:
            successes+=1
            assert len(proof['confirmed_snapshots'])==len(proof['confirmation_disclosures'])==len(proof['operation_receipts'])==1
            sid=proof['confirmed_snapshots'][0]['snapshot_id']
            assert proof['working_drafts'][0]['latest_confirmed_version_id']==sid
            assert proof['confirmation_disclosures'][0]['snapshot_id']==sid
            assert len([c for c in proof['api_commands'] if c['path'].endswith('/confirmations')])==1
    assert successes==4
    passwords=[]
    for path in (root/'.local').glob('m105b-isolated-*.env'):
        for line in path.read_text().splitlines():
            if line.startswith(('EASYTRAVEL_DATABASE_URL=','EASYTRAVEL_TEST_DATABASE_URL=')):
                passwords.append(conninfo_to_dict(line.split('=',1)[1])['password'])
    for path in (root/'Docs').glob('M1-05b*'):
        if path.suffix in {'.json','.txt','.md'}:
            data=path.read_text(encoding='utf-8-sig')
            assert all(password not in data for password in passwords),'Database password in evidence'
    report=dict(result='PASS',baseline=BASELINE,backendAndMigrationsUnchanged=True,migrationSHA256=migrations,
        reviewedEvidenceUnchanged=True,isolatedContainers=isolation,oldExperimentFingerprintsUnchanged=True,
        transportHookRemoved=True,databasePasswordsAbsentFromEvidence=True,backendTests=384,targetedBackendTests=26,
        frontendTests=115,browserTests=53,browserEvidenceCases=len(records),uniqueConfirmedTripsVerified=successes)
    (root/'Docs/M1-05b_完整性核验.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('M1-05b final integrity: PASS; backend and all 5 migrations unchanged; evidence and isolation verified')

if __name__=='__main__':main()
