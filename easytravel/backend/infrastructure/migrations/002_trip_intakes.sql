-- Append-only user input and completed HTTP command receipts. No draft fabrication.
CREATE TABLE easytravel.trip_intake_versions (
    trip_id text NOT NULL,
    owner_id text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    input jsonb NOT NULL CHECK (jsonb_typeof(input) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (trip_id, revision),
    UNIQUE (trip_id, owner_id, revision),
    FOREIGN KEY (trip_id, owner_id) REFERENCES easytravel.trips(trip_id, owner_id)
);
CREATE TABLE easytravel.trip_intakes (
    trip_id text PRIMARY KEY,
    owner_id text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    FOREIGN KEY (trip_id, owner_id, revision)
        REFERENCES easytravel.trip_intake_versions(trip_id, owner_id, revision) DEFERRABLE
);
CREATE TABLE easytravel.api_commands (
    command_id uuid PRIMARY KEY,
    operation_id uuid NOT NULL UNIQUE,
    owner_id text NOT NULL,
    method text NOT NULL CHECK (method = 'POST'),
    path text NOT NULL CHECK (path = '/api/v1/trips'),
    idempotency_key uuid NOT NULL,
    request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    trip_id text NOT NULL,
    response_status smallint NOT NULL CHECK (response_status = 201),
    result jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'
        AND result ? 'trip_id' AND result->>'trip_id' = trip_id),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (owner_id, method, path, idempotency_key),
    FOREIGN KEY (trip_id, owner_id) REFERENCES easytravel.trips(trip_id, owner_id)
);
CREATE TRIGGER immutable_intake_version BEFORE UPDATE OR DELETE ON easytravel.trip_intake_versions
    FOR EACH ROW EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE TRIGGER no_truncate_intake_version BEFORE TRUNCATE ON easytravel.trip_intake_versions
    FOR EACH STATEMENT EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE TRIGGER immutable_api_command BEFORE UPDATE OR DELETE ON easytravel.api_commands
    FOR EACH ROW EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE TRIGGER no_truncate_api_command BEFORE TRUNCATE ON easytravel.api_commands
    FOR EACH STATEMENT EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE FUNCTION easytravel.advance_intake_once() RETURNS trigger LANGUAGE plpgsql
    SET search_path = pg_catalog AS $$
BEGIN
    IF NEW.trip_id <> OLD.trip_id OR NEW.owner_id <> OLD.owner_id
        OR NEW.revision <> OLD.revision + 1 THEN
        RAISE EXCEPTION 'invalid_intake_revision_transition' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER intake_revision_transition BEFORE UPDATE ON easytravel.trip_intakes
    FOR EACH ROW EXECUTE FUNCTION easytravel.advance_intake_once();
CREATE TRIGGER immutable_intake_pointer BEFORE DELETE ON easytravel.trip_intakes
    FOR EACH ROW EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE TRIGGER no_truncate_intake_pointer BEFORE TRUNCATE ON easytravel.trip_intakes
    FOR EACH STATEMENT EXECUTE FUNCTION easytravel.forbid_mutation();
