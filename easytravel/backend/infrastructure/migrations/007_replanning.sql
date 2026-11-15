-- Immutable v2 context selects the server-side validation branch. v1 hash is untouched.
ALTER TABLE easytravel.generation_runs DROP CONSTRAINT generation_runs_kind_check;
ALTER TABLE easytravel.generation_runs ADD CHECK(kind IN ('INITIAL_PLAN','CHANGE_INTENT','REPLAN'));
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='easytravel.generation_runs'::regclass
 AND contype='c' AND pg_get_constraintdef(oid) LIKE '%base_working_revision%'
 LOOP EXECUTE format('ALTER TABLE easytravel.generation_runs DROP CONSTRAINT %I',c.conname); END LOOP;
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='easytravel.generation_runs'::regclass
 AND contype='c' AND pg_get_constraintdef(oid) LIKE '%working_revision%'
 LOOP EXECUTE format('ALTER TABLE easytravel.generation_runs DROP CONSTRAINT %I',c.conname); END LOOP;
END $$;
ALTER TABLE easytravel.generation_runs ADD CHECK(COALESCE(
 (kind='INITIAL_PLAN' AND base_working_revision IS NULL) OR
 (kind IN ('CHANGE_INTENT','REPLAN') AND base_working_revision>0),false));
ALTER TABLE easytravel.generation_runs ADD CHECK(COALESCE(
 (status IN ('ACCEPTED','RUNNING') AND result IS NULL AND error IS NULL) OR
 (status='FAILED' AND result IS NULL AND error IS NOT NULL) OR
 (status='SUCCEEDED' AND error IS NULL AND result IS NOT NULL AND
  ((kind='INITIAL_PLAN' AND result->>'working_revision'='1' AND result->>'proposal_id' IS NULL) OR
   (kind IN ('CHANGE_INTENT','REPLAN') AND result->>'working_revision' IS NULL AND result->>'proposal_id' IS NOT NULL))),false));
CREATE TABLE easytravel.replan_inputs (
 run_id text PRIMARY KEY,
 trip_id text NOT NULL,
 owner_id text NOT NULL,
 base_working_revision integer NOT NULL,
 source jsonb NOT NULL CHECK(jsonb_typeof(source)='object'),
 FOREIGN KEY(run_id,trip_id,owner_id,base_working_revision) REFERENCES easytravel.generation_runs(run_id,trip_id,owner_id,base_working_revision)
);
CREATE TABLE easytravel.replan_contexts (
 proposal_id text PRIMARY KEY,
 trip_id text NOT NULL,
 owner_id text NOT NULL,
 context_v2 jsonb NOT NULL CHECK(jsonb_typeof(context_v2)='object' AND context_v2->>'schema_version'='2'),
 FOREIGN KEY(proposal_id,trip_id,owner_id) REFERENCES easytravel.change_proposals(proposal_id,trip_id,owner_id)
);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['replan_inputs','replan_contexts'] LOOP
  EXECUTE format('CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON easytravel.%I FOR EACH ROW EXECUTE FUNCTION easytravel.forbid_mutation()',t);
  EXECUTE format('CREATE TRIGGER no_truncate BEFORE TRUNCATE ON easytravel.%I FOR EACH STATEMENT EXECUTE FUNCTION easytravel.forbid_mutation()',t);
 END LOOP;
END $$;
