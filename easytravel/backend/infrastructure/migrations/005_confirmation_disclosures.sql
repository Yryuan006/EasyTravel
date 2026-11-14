-- Additive disclosure facts; legacy snapshots intentionally remain without a record.
ALTER TABLE easytravel.api_commands DROP CONSTRAINT api_commands_check;
ALTER TABLE easytravel.api_commands ADD CONSTRAINT api_commands_result_trip_check CHECK (
 jsonb_typeof(result)='object' AND
 CASE WHEN path ~ '^/api/v1/trips/[A-Za-z0-9_-]{1,80}/confirmations$'
 THEN COALESCE(result #>> '{snapshot,snapshot,trip_id}' = trip_id, false)
 ELSE result ? 'trip_id' AND result->>'trip_id'=trip_id END);
ALTER TABLE easytravel.api_commands DROP CONSTRAINT api_commands_route_check;
ALTER TABLE easytravel.api_commands ADD CONSTRAINT api_commands_route_check CHECK (
 (method='POST' AND path='/api/v1/trips' AND response_status=201) OR
 (method='PUT' AND path ~ '^/api/v1/trips/[A-Za-z0-9_-]{1,80}/requirements$' AND response_status=200) OR
 (method='POST' AND path ~ '^/api/v1/trips/[A-Za-z0-9_-]{1,80}/clarifications/[A-Za-z0-9_-]{1,80}/answers$' AND response_status=200) OR
 (method='POST' AND path ~ '^/api/v1/trips/[A-Za-z0-9_-]{1,80}/generations$' AND response_status=202) OR
 (method='POST' AND path ~ '^/api/v1/trips/[A-Za-z0-9_-]{1,80}/confirmations$' AND response_status=201));
ALTER TABLE easytravel.confirmed_snapshots ADD CONSTRAINT snapshot_source_identity
 UNIQUE(snapshot_id,trip_id,confirmed_by,source_working_revision);
ALTER TABLE easytravel.draft_contexts ADD CONSTRAINT context_source_identity
 UNIQUE(trip_id,owner_id,working_revision,intake_revision);
CREATE TABLE easytravel.confirmation_disclosures (
 snapshot_id text PRIMARY KEY,
 trip_id text NOT NULL,
 owner_id text NOT NULL,
 working_revision integer NOT NULL,
 intake_revision integer NOT NULL,
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 risk_hash text NOT NULL CHECK(risk_hash ~ '^[0-9a-f]{64}$'),
 disclosure jsonb NOT NULL CHECK(jsonb_typeof(disclosure)='object'
   AND disclosure->>'risk_hash'=risk_hash AND disclosure->>'input_hash'=input_hash),
 context jsonb NOT NULL CHECK(jsonb_typeof(context)='object'
   AND (context->>'intake_revision')::integer=intake_revision),
 FOREIGN KEY(snapshot_id,trip_id,owner_id,working_revision) REFERENCES easytravel.confirmed_snapshots(snapshot_id,trip_id,confirmed_by,source_working_revision),
 FOREIGN KEY(trip_id,owner_id,working_revision) REFERENCES easytravel.draft_versions(trip_id,owner_id,revision),
 FOREIGN KEY(trip_id,owner_id,working_revision,intake_revision) REFERENCES easytravel.draft_contexts(trip_id,owner_id,working_revision,intake_revision),
 FOREIGN KEY(trip_id,owner_id,intake_revision) REFERENCES easytravel.trip_intake_versions(trip_id,owner_id,revision)
);
CREATE TRIGGER immutable_confirmation_disclosure BEFORE UPDATE OR DELETE ON easytravel.confirmation_disclosures
 FOR EACH ROW EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE TRIGGER no_truncate_confirmation_disclosure BEFORE TRUNCATE ON easytravel.confirmation_disclosures
 FOR EACH STATEMENT EXECUTE FUNCTION easytravel.forbid_mutation();
