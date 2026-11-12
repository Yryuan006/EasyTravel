-- Durable single-executor generation runs; no startup migration or experiment dependency.
ALTER TABLE easytravel.api_commands DROP CONSTRAINT api_commands_route_check;
ALTER TABLE easytravel.api_commands ADD CONSTRAINT api_commands_route_check CHECK (
 (method='POST' AND path='/api/v1/trips' AND response_status=201) OR
 (method='PUT' AND path ~ '^/api/v1/trips/[A-Za-z0-9_-]{1,80}/requirements$' AND response_status=200) OR
 (method='POST' AND path ~ '^/api/v1/trips/[A-Za-z0-9_-]{1,80}/clarifications/[A-Za-z0-9_-]{1,80}/answers$' AND response_status=200) OR
 (method='POST' AND path ~ '^/api/v1/trips/[A-Za-z0-9_-]{1,80}/generations$' AND response_status=202));
CREATE TABLE easytravel.generation_runs (
 run_id text PRIMARY KEY,
 trip_id text NOT NULL,
 owner_id text NOT NULL,
 intake_revision integer NOT NULL,
 kind text NOT NULL CHECK(kind='INITIAL_PLAN'),
 status text NOT NULL CHECK(status IN ('ACCEPTED','RUNNING','SUCCEEDED','FAILED')),
 attempt integer NOT NULL CHECK(attempt>0),
 retry_of text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 result jsonb,
 error jsonb,
 diagnostics_budget jsonb,
 UNIQUE(run_id,trip_id,owner_id),
 FOREIGN KEY(trip_id,owner_id,intake_revision) REFERENCES easytravel.trip_intake_versions(trip_id,owner_id,revision),
 FOREIGN KEY(retry_of,trip_id,owner_id) REFERENCES easytravel.generation_runs(run_id,trip_id,owner_id),
 CHECK ((status IN ('ACCEPTED','RUNNING') AND result IS NULL AND error IS NULL) OR
        (status='SUCCEEDED' AND result IS NOT NULL AND error IS NULL AND result->>'working_revision'='1' AND result->>'proposal_id' IS NULL) OR
        (status='FAILED' AND result IS NULL AND error IS NOT NULL))
);
CREATE UNIQUE INDEX one_active_generation_per_trip ON easytravel.generation_runs(trip_id) WHERE status IN ('ACCEPTED','RUNNING');
CREATE TABLE easytravel.draft_contexts (
 trip_id text NOT NULL,
 owner_id text NOT NULL,
 working_revision integer NOT NULL,
 intake_revision integer NOT NULL,
 run_id text NOT NULL UNIQUE,
 context jsonb NOT NULL CHECK(jsonb_typeof(context)='object' AND (context->>'intake_revision')::integer=intake_revision),
 PRIMARY KEY(trip_id,working_revision),
 FOREIGN KEY(trip_id,owner_id,working_revision) REFERENCES easytravel.draft_versions(trip_id,owner_id,revision),
 FOREIGN KEY(trip_id,owner_id,intake_revision) REFERENCES easytravel.trip_intake_versions(trip_id,owner_id,revision),
 FOREIGN KEY(run_id,trip_id,owner_id) REFERENCES easytravel.generation_runs(run_id,trip_id,owner_id)
);
CREATE TRIGGER immutable_draft_context BEFORE UPDATE OR DELETE ON easytravel.draft_contexts
 FOR EACH ROW EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE TRIGGER no_truncate_draft_context BEFORE TRUNCATE ON easytravel.draft_contexts
 FOR EACH STATEMENT EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE FUNCTION easytravel.generation_transition() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 IF (to_jsonb(NEW)-ARRAY['status','updated_at','result','error','diagnostics_budget']) IS DISTINCT FROM
    (to_jsonb(OLD)-ARRAY['status','updated_at','result','error','diagnostics_budget']) OR
    NOT ((OLD.status='ACCEPTED' AND NEW.status IN ('RUNNING','FAILED')) OR
         (OLD.status='RUNNING' AND NEW.status IN ('SUCCEEDED','FAILED'))) THEN
   RAISE EXCEPTION 'invalid_generation_transition' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER generation_transition BEFORE UPDATE ON easytravel.generation_runs
 FOR EACH ROW EXECUTE FUNCTION easytravel.generation_transition();
CREATE TRIGGER immutable_generation_delete BEFORE DELETE ON easytravel.generation_runs
 FOR EACH ROW EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE TRIGGER no_truncate_generation BEFORE TRUNCATE ON easytravel.generation_runs
 FOR EACH STATEMENT EXECUTE FUNCTION easytravel.forbid_mutation();
