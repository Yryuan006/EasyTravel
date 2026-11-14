"""Generate/check confirmation TypeScript and runtime schemas from accepted OpenAPI."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def artifacts():
    spec=json.loads((ROOT/'Docs/M1-05a_openapi.json').read_text(encoding='utf-8'))
    schemas=spec['components']['schemas']; names=set()
    def visit(s):
        if isinstance(s,dict):
            if '$ref' in s:
                n=s['$ref'].split('/')[-1]
                if n not in names:names.add(n);visit(schemas[n])
            for v in s.values():visit(v)
        elif isinstance(s,list):
            for v in s:visit(v)
    for name in ['ConfirmationPreview','ConfirmRequest','ConfirmResult']:
        visit({'$ref':'#/components/schemas/'+name})
    def ts(s):
        if '$ref' in s:return s['$ref'].split('/')[-1]
        if 'const' in s:return json.dumps(s['const'],ensure_ascii=False)
        if 'enum' in s:return ' | '.join(json.dumps(v,ensure_ascii=False) for v in s['enum'])
        for key in ['anyOf','oneOf']:
            if key in s:return ' | '.join('('+ts(v)+')' for v in s[key])
        kind=s.get('type')
        if kind=='object':
            return '{ '+ '; '.join(k+('' if k in s.get('required',[]) else '?')+': '+ts(v) for k,v in s.get('properties',{}).items())+' }'
        if kind=='array':
            return '['+', '.join(ts(v) for v in s['prefixItems'])+']' if 'prefixItems' in s else '('+ts(s.get('items',{}))+')[]'
        return {'integer':'number','number':'number','string':'string','boolean':'boolean','null':'null'}.get(kind,'unknown')
    data={n:schemas[n] for n in sorted(names)}
    types='// Generated from M1-05a actual OpenAPI; run scripts/verify_m105b_contract.py --write.\n'
    types+='\n'.join('export type '+n+' = '+ts(data[n]) for n in data)+'\n'
    return data,types

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--write',action='store_true');args=parser.parse_args()
    data,types=artifacts()
    files={'frontend/src/api/confirmation-schema.json':json.dumps(data,ensure_ascii=False,indent=2)+'\n',
           'frontend/src/api/confirmation-contracts.ts':types}
    for path,content in files.items():
        p=ROOT/path
        if args.write:p.write_text(content,encoding='utf-8')
        assert p.read_text(encoding='utf-8')==content,path+' differs from actual OpenAPI'
    # Business API and accepted migrations remain unchanged.
    from easytravel.backend.main import create_app
    from easytravel.backend.config import Settings
    actual=create_app(Settings(environment='test')).openapi()
    assert actual==json.loads((ROOT/'Docs/M1-05a_openapi.json').read_text(encoding='utf-8'))
    report=dict(result='PASS',source='M1-05a actual OpenAPI',schemas=len(data),generatedFiles=list(files),
                actualBackendOpenAPIUnchanged=True,sha256={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in files})
    (ROOT/'Docs/M1-05b_契约核对.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Confirmation TypeScript/runtime schema contract: PASS')

if __name__=='__main__':main()
