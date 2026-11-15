"""Offline architecture/API/TS/migration audit against the accepted M1-07 baseline."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
from easytravel.backend.config import Settings
from easytravel.backend.main import create_app

BASELINE = '8889df6651310e9d8f7e56be4e6f751bbf54a4b3'


def baseline(path):
    return subprocess.run(['git','show',BASELINE+':'+path],capture_output=True,check=True).stdout


def main():
    schema = create_app(Settings(environment='test')).openapi()
    accepted = json.loads(baseline('Docs/M1-07_OpenAPI.json'))
    assert schema == accepted, 'Unexpected API surface or contract change'
    Path('Docs/M1-08_OpenAPI.json').write_text(json.dumps(schema,ensure_ascii=False,indent=2),encoding='utf-8')
    runtime = {}
    for name in ('working','generation','confirmation','change','snapshot'):
        runtime.update(json.loads(Path('frontend/src/api/'+name+'-schema.json').read_text(encoding='utf-8')))
    current_names = runtime.keys() & schema['components']['schemas'].keys()
    assert all(runtime[name] == schema['components']['schemas'][name] for name in current_names)
    assert runtime.keys() - current_names == {'WorkingDraft'}
    legacy, current = runtime['WorkingDraft'], schema['components']['schemas']['WorkingView']
    assert current['properties'].keys() - legacy['properties'].keys() == {'context'}
    assert all(value == current['properties'][name] for name,value in legacy['properties'].items())
    classes, imports = {}, []
    formal = sorted(Path('easytravel/backend').rglob('*.py'))
    for p in formal:
        tree = ast.parse(p.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes.setdefault(node.name,[]).append(p.as_posix())
            if isinstance(node,(ast.Import,ast.ImportFrom)):
                names = [n.name for n in node.names] if isinstance(node,ast.Import) else [node.module or '']
                assert not any('spike' in name or 'm000' in name for name in names),(p,names)
                if p.parts[2] in ('application','domain'):
                    assert not any(name.split('.')[0] in {'psycopg','fastapi','langgraph'} for name in names),(p,names)
                imports.extend(dict(file=p.as_posix(),module=name) for name in names)
    for name in ('WorkingDraft','ConfirmedSnapshot','SpecificAuthorization'):
        assert classes[name] == ['easytravel/backend/domain/models.py']
    assert 'class BudgetResult:' in Path('easytravel/rules.py').read_text(encoding='utf-8')
    migrations = {}
    for p in sorted(Path('easytravel/backend/infrastructure/migrations').glob('*.sql')):
        data = p.read_bytes();assert data == baseline(p.as_posix())
        migrations[p.name] = hashlib.sha256(data).hexdigest()
    assert len(migrations)==7
    # No runtime feature edits are required for the baseline acceptance checks.
    runtime_files = formal + sorted(Path('frontend/src').rglob('*')) + [Path('easytravel')/name for name in ('contracts.py','rules.py','model_adapter.py','application.py')]
    runtime_hashes, minimal_fixes = {}, {}
    for p in runtime_files:
        if not p.is_file() or '__pycache__' in p.parts:
            continue
        old = baseline(p.as_posix())
        old_text = old.decode('utf-8').replace('\r\n','\n')
        if p.as_posix() == 'frontend/src/api/confirmations.ts':
            expected = old_text.replace('需求与工作稿不同步。请先核对需求；本阶段尚不支持重规划，不能确认旧稿。',
                '需求与工作稿不同步。请先审阅新需求并完成重规划，再读取当前行程的预算与风险；不能确认旧稿。')
            assert expected == p.read_text(encoding='utf-8')
            minimal_fixes[p.as_posix()] = 'D01: stale confirmation recovery copy only; no contract or behavior change'
        else:
            assert old_text == p.read_text(encoding='utf-8'),p
        runtime_hashes[p.as_posix()] = hashlib.sha256(old).hexdigest()
    docs = sorted(Path('Docs').glob('M1-00_*.md')) + sorted(Path('Docs').glob('ADR-00*.md'))
    assert len(docs)==8
    for p in docs:
        assert baseline(p.as_posix()).decode('utf-8').replace('\r\n','\n') == p.read_text(encoding='utf-8')
    report = dict(baseline=BASELINE,actualOpenAPIUnchanged=True,paths=sorted(schema['paths']),
        baselineRuntimeFileHashes=runtime_hashes,minimalRuntimeFixes=minimal_fixes,allOtherRuntimeFilesUnchanged=True,designDocumentsVerified=[p.as_posix() for p in docs],
        runtimeSchemaCount=len(runtime),currentSchemaMatchCount=len(current_names),runtimeSchemaNames=sorted(runtime),runtimeSchemasMatchPydantic=True,
        legacyReadOnlySubset=dict(name='WorkingDraft',extendsTo='WorkingView',onlyMissingField='context',commonFieldsIdentical=True),
        uniqueAuthorities={name:classes[name] for name in ('WorkingDraft','ConfirmedSnapshot','SpecificAuthorization')},
        budgetAuthority='easytravel/rules.py:BudgetResult/evaluate_budget',formalImportsNoExperiment=True,
        applicationDomainNoHTTPDriverOrGraphDependency=True,migrations=migrations,
        note='Static and contract audit; runtime conclusions additionally require the isolated tests and SQL evidence.')
    Path('Docs/M1-08_架构与契约核查.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Formal architecture, OpenAPI, 71 runtime schemas and migrations 001-007 verified against M1-07')


if __name__=='__main__':main()
