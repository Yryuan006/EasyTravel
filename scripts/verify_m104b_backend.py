"""Run the full backend suite against three fresh retained databases; never reset data."""
import argparse, os, subprocess, sys
from pathlib import Path
from m104b_evidence import preserve_accepted_evidence

def load(path):
    return dict(line.split('=',1) for line in Path(path).read_text(encoding='utf-8-sig').splitlines() if line and not line.startswith('#'))

parser=argparse.ArgumentParser()
parser.add_argument('--database-prefix',required=True,help='Prefix of three NEW -api/-regression/-compat.env files')
parser.add_argument('--experiment-env',required=True)
parser.add_argument('--retained-env',required=True)
args=parser.parse_args()
prefix=args.database_prefix
env=os.environ.copy()|load(args.experiment_env)|load(prefix+'-regression.env')
env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
env['EASYTRAVEL_M104_TEST_DATABASE_URL']=load(prefix+'-gen.env')['EASYTRAVEL_DATABASE_URL']
env['EASYTRAVEL_M101_TEST_DATABASE_URL']=load(prefix+'-api.env')['EASYTRAVEL_DATABASE_URL']
env['EASYTRAVEL_M103_TEST_DATABASE_URL']=load(prefix+'-compat.env')['EASYTRAVEL_DATABASE_URL']
env['EASYTRAVEL_M101_RETAINED_DATABASE_URL']=load(args.retained_env)['EASYTRAVEL_DATABASE_URL']
with preserve_accepted_evidence(), Path('Docs/M1-04b_后端全量回归.txt').open('w',encoding='utf-8') as stream:
    result=subprocess.run([sys.executable,'-m','pytest','-v','-s'],env=env,stdout=stream,stderr=subprocess.STDOUT)
print('Backend regression exit:',result.returncode)
print('\n'.join(Path('Docs/M1-04b_后端全量回归.txt').read_text(encoding='utf-8').splitlines()[-24:]))
raise SystemExit(result.returncode)
