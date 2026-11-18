"""Pinned local frontend CLIs with Node DNS/TCP/HTTP guards; no npm notifier."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'Docs/M2-01c'
GUARD = """const fs = require('node:fs');
let attempts = 0;
function blocked() { attempts++; throw new Error('offline frontend guard'); }
require('node:net').Socket.prototype.connect = blocked;
const dns = require('node:dns');
for (const k of Object.keys(dns)) if (typeof dns[k] === 'function') dns[k] = blocked;
for (const k of Object.keys(dns.promises)) if (typeof dns.promises[k] === 'function') dns.promises[k] = blocked;
for (const name of ['node:http','node:https']) {
  const mod = require(name); mod.request = blocked; mod.get = blocked;
}
process.on('exit', () => fs.appendFileSync(process.env.M201C_NODE_GUARD_LOG,
 JSON.stringify({pid:process.pid, attempts})+'\\n'));
"""


def main():
    OUT.mkdir(exist_ok=True)
    node = ROOT / '.tools/node-m003/node_modules/node/bin/node.exe'
    assert subprocess.run([str(node),'--version'],capture_output=True,text=True,check=True).stdout.strip() == 'v24.22.2'
    guard = ROOT / '.local/m201c-node-guard.cjs'
    guard.write_text(GUARD, encoding='utf-8')
    report_file = ROOT / '.local/m201c-node-network.jsonl'
    report_file.write_text('',encoding='utf-8')
    env = os.environ | dict(NODE_OPTIONS='--require='+str(guard), M201C_NODE_GUARD_LOG=str(report_file),
                            NPM_CONFIG_UPDATE_NOTIFIER='false',NPM_CONFIG_OFFLINE='true')
    commands = [('TypeScript.txt', [
        ['node_modules/vue-tsc/bin/vue-tsc.js','--noEmit'],
        ['node_modules/typescript/bin/tsc','-p','tsconfig.node.json']]),
        ('Build.txt', [['node_modules/vite/bin/vite.js','build']])]
    for filename, steps in commands:
        with (OUT/filename).open('w',encoding='utf-8') as log:
            for args in steps:
                print('Pinned Node 24.22.2, local CLI, DNS/TCP/HTTP denied:', ' '.join(args),file=log,flush=True)
                subprocess.run([str(node),*args],cwd=ROOT/'frontend',env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    records = [json.loads(line) for line in report_file.read_text().splitlines()]
    assert records and all(r['attempts'] == 0 for r in records)
    (OUT/'前端离线验证.json').write_text(json.dumps(dict(node='v24.22.2',npmNotInvoked=True,
        localCLIsOnly=True,networkGuardsEnabled=True,processes=records,frontendSourceChanged=False),indent=2)+'\n',encoding='utf-8')
    print('PASS frontend typecheck/build, local CLIs, zero network attempts')


if __name__ == '__main__':
    main()
