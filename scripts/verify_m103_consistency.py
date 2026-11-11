"""Read-only cross-table audit of the retained M1-03a test database."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import psycopg
from psycopg.rows import dict_row

parser=argparse.ArgumentParser();parser.add_argument('--database-env',required=True);args=parser.parse_args()
env=dict(line.split('=',1) for line in Path(args.database_env).read_text(encoding='utf-8').splitlines() if line)
with psycopg.connect(env['EASYTRAVEL_DATABASE_URL'],row_factory=dict_row) as conn:
    conn.execute('SET TRANSACTION READ ONLY')
    trips=conn.execute("""SELECT i.trip_id,i.revision,count(v.revision) AS version_count,min(v.revision) AS first_revision,max(v.revision) AS last_revision
      FROM easytravel.trip_intakes i JOIN easytravel.trip_intake_versions v USING(trip_id,owner_id)
      GROUP BY i.trip_id,i.revision ORDER BY i.trip_id""").fetchall()
    assert all(t['first_revision']==1 and t['version_count']==t['last_revision']==t['revision'] for t in trips)
    questions=conn.execute("""SELECT v.trip_id,v.revision,count(q.question_id) AS descriptors
      FROM easytravel.trip_intake_versions v LEFT JOIN easytravel.clarification_questions q
       ON (v.trip_id,v.owner_id,v.revision)=(q.trip_id,q.owner_id,q.intake_revision)
      GROUP BY v.trip_id,v.revision""").fetchall()
    assert all(q['descriptors']==10 for q in questions)
    answers=conn.execute("""SELECT a.question_id::text,a.trip_id,a.intake_revision,a.resulting_revision,a.field_key,
      a.answer=v.input->a.field_key AS fact_matches, c.result->'intake'->>'intake_revision' AS receipt_revision
      FROM easytravel.clarification_answers a JOIN easytravel.trip_intake_versions v
        ON (a.trip_id,a.owner_id,a.resulting_revision)=(v.trip_id,v.owner_id,v.revision)
      JOIN easytravel.api_commands c ON a.command_id=c.command_id ORDER BY a.trip_id,a.intake_revision""").fetchall()
    assert all(a['fact_matches'] and int(a['receipt_revision'])==a['resulting_revision']==a['intake_revision']+1 for a in answers)
    inactive=conn.execute("""SELECT count(*) n FROM easytravel.clarification_questions q
       JOIN easytravel.trip_intakes i USING(trip_id,owner_id) WHERE q.intake_revision<>i.revision""").fetchone()['n']
    counts={name:conn.execute(f'SELECT count(*) n FROM easytravel.{name}').fetchone()['n'] for name in
       ('trips','trip_intake_versions','clarification_questions','clarification_answers','api_commands','draft_versions','confirmed_snapshots')}
unchanged={}
for name in ('001_initial.sql','002_trip_intakes.sql'):
    path='easytravel/backend/infrastructure/migrations/'+name
    old=subprocess.run(['git','show','f66e850d4a51c78753ece167f6d4f45ee218eb94:'+path],check=True,capture_output=True).stdout
    current=Path(path).read_bytes();assert current==old
    unchanged[name]=hashlib.sha256(current).hexdigest()
report=dict(checks='PASS: contiguous immutable revisions/current pointers, 10 descriptors per version, answer/result/receipt binding, original migrations byte-identical',
            counts=counts,invalidated_question_descriptors=inactive,trips=trips,answers=answers,baseline_migration_sha256=unchanged)
Path('Docs/M1-03a_数据库一致性.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(report['checks']);print('Retained test counts:',json.dumps(counts));print('Old-version descriptors safely inactive:',inactive)
