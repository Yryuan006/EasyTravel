-- Extend the existing single-executor run and immutable v1 proposal mechanisms.
ALTER TABLE easytravel.api_commands DROP CONSTRAINT api_commands_result_trip_check;
ALTER TABLE easytravel.api_commands ADD CONSTRAINT api_commands_result_trip_check CHECK (
 jsonb_typeof(result)='object' AND
 CASE WHEN path ~ '^/api/v1/trips/[A-Za-z0-9_-]{1,80}/confirmations$'
 THEN COALESCE(result #>> '{snapshot,snapshot,trip_id}' = trip_id,false)
 WHEN path ~ '^/api/v1/trips/[A-Za-z0-9_-]{1,80}/proposals/[A-Za-z0-9_-]{1,80}/decisions$'
 THEN COALESCE(path='/api/v1/trips/' || trip_id || '/proposals/' || (result->>'proposal_id') || '/decisions',false)
 ELSE result ? 'trip_id' AND result->>'trip_id'=trip_id END);
ALTER TABLE easytravel.generation_runs ADD COLUMN base_working_revision integer;
ALTER TABLE easytravel.generation_runs ADD FOREIGN KEY (trip_id,owner_id,base_working_revision)
 REFERENCES easytravel.draft_versions(trip_id,owner_id,revision);
ALTER TABLE easytravel.generation_runs ADD UNIQUE(run_id,trip_id,owner_id,base_working_revision);
ALTER TABLE easytravel.generation_runs DROP CONSTRAINT generation_runs_kind_check;
ALTER TABLE easytravel.generation_runs ADD CHECK (kind IN ('INITIAL_PLAN','CHANGE_INTENT'));
-- Remove only the original combined state/result check (not field constraints).
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='easytravel.generation_runs'::regclass
   AND contype='c' AND pg_get_constraintdef(oid) LIKE '%working_revision%'
 LOOP EXECUTE format('ALTER TABLE easytravel.generation_runs DROP CONSTRAINT %I',c.conname); END LOOP;
END $$;
ALTER TABLE easytravel.generation_runs ADD CHECK (COALESCE((
 (kind='INITIAL_PLAN' AND base_working_revision IS NULL) OR
 (kind='CHANGE_INTENT' AND base_working_revision>0)),false));
ALTER TABLE easytravel.generation_runs ADD CHECK (COALESCE((
 (status IN ('ACCEPTED','RUNNING') AND result IS NULL AND error IS NULL) OR
 (status='FAILED' AND result IS NULL AND error IS NOT NULL) OR
 (status='SUCCEEDED' AND result IS NOT NULL AND error IS NULL AND
   ((kind='INITIAL_PLAN' AND result->>'working_revision'='1' AND result->>'proposal_id' IS NULL) OR
    (kind='CHANGE_INTENT' AND result->>'working_revision' IS NULL AND result->>'proposal_id' IS NOT NULL)))),false));
CREATE TABLE easytravel.change_run_inputs (
 run_id text PRIMARY KEY,
 trip_id text NOT NULL,
 owner_id text NOT NULL,
 base_working_revision integer NOT NULL,
 intent text NOT NULL CHECK(length(intent) BETWEEN 1 AND 2000),
 source jsonb NOT NULL CHECK(jsonb_typeof(source)='object' AND
   COALESCE((source->>'revision')::integer=base_working_revision AND source->>'trip_id'=trip_id AND source->>'owner_id'=owner_id,false)),
 FOREIGN KEY(run_id,trip_id,owner_id,base_working_revision) REFERENCES easytravel.generation_runs(run_id,trip_id,owner_id,base_working_revision),
 FOREIGN KEY(trip_id,owner_id,base_working_revision) REFERENCES easytravel.draft_versions(trip_id,owner_id,revision)
);
ALTER TABLE easytravel.change_proposals ADD UNIQUE(proposal_id,trip_id,owner_id);
CREATE TABLE easytravel.proposal_contexts (
 proposal_id text PRIMARY KEY,
 trip_id text NOT NULL,
 owner_id text NOT NULL,
 run_id text NOT NULL UNIQUE,
 context jsonb NOT NULL CHECK(jsonb_typeof(context)='object'),
 FOREIGN KEY(proposal_id,trip_id,owner_id) REFERENCES easytravel.change_proposals(proposal_id,trip_id,owner_id),
 FOREIGN KEY(run_id,trip_id,owner_id) REFERENCES easytravel.generation_runs(run_id,trip_id,owner_id)
);
CREATE TABLE easytravel.proposal_decisions (
 proposal_id text PRIMARY KEY,
 trip_id text NOT NULL,
 owner_id text NOT NULL,
 decision text NOT NULL CHECK(decision IN ('approve','reject')),
 operation_id text NOT NULL UNIQUE,
 request_hash text NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
 result jsonb NOT NULL CHECK(jsonb_typeof(result)='object' AND COALESCE(
   result->>'proposal_id'=proposal_id AND result->>'decision'=decision AND result->>'operation_id'=operation_id
   AND result->>'state'=CASE decision WHEN 'approve' THEN 'APPLIED' ELSE 'REJECTED' END,false)),
 decided_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(proposal_id,trip_id,owner_id) REFERENCES easytravel.change_proposals(proposal_id,trip_id,owner_id),
 FOREIGN KEY(operation_id,trip_id,owner_id) REFERENCES easytravel.operation_receipts(operation_id,trip_id,owner_id)
   DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE easytravel.operation_receipts DROP CONSTRAINT operation_receipts_operation_type_check;
ALTER TABLE easytravel.operation_receipts ADD CHECK(operation_type IN ('apply_working_change','confirm_plan','reject_proposal'));
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['change_run_inputs','proposal_contexts','proposal_decisions'] LOOP
  EXECUTE format('CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON easytravel.%I FOR EACH ROW EXECUTE FUNCTION easytravel.forbid_mutation()',t);
  EXECUTE format('CREATE TRIGGER no_truncate BEFORE TRUNCATE ON easytravel.%I FOR EACH STATEMENT EXECUTE FUNCTION easytravel.forbid_mutation()',t);
 END LOOP;
END $$;
ALTER TABLE easytravel.api_commands DROP CONSTRAINT api_commands_route_check;
ALTER TABLE easytravel.api_commands ADD CONSTRAINT api_commands_route_check CHECK (
 (method='POST' AND path='/api/v1/trips' AND response_status=201) OR
 (method='PUT' AND path ~ '^/api/v1/trips/[A-Za-z0-9_-]{1,80}/requirements$' AND response_status=200) OR
 (method='POST' AND path ~ '^/api/v1/trips/[A-Za-z0-9_-]{1,80}/clarifications/[A-Za-z0-9_-]{1,80}/answers$' AND response_status=200) OR
 (method='POST' AND path ~ '^/api/v1/trips/[A-Za-z0-9_-]{1,80}/(generations|change-intents)$' AND response_status=202) OR
 (method='POST' AND path ~ '^/api/v1/trips/[A-Za-z0-9_-]{1,80}/confirmations$' AND response_status=201) OR
 (method='POST' AND path ~ '^/api/v1/trips/[A-Za-z0-9_-]{1,80}/proposals/[A-Za-z0-9_-]{1,80}/decisions$' AND response_status=200));
