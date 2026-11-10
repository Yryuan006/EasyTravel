-- Formal business schema. No references to experiment tables or Checkpoints.
CREATE TABLE easytravel.trips (
    trip_id text PRIMARY KEY CHECK (trip_id ~ '^[A-Za-z0-9_-]{1,80}$'),
    owner_id text NOT NULL CHECK (owner_id ~ '^[A-Za-z0-9_-]{1,80}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (trip_id, owner_id)
);
CREATE TABLE easytravel.draft_versions (
    trip_id text NOT NULL,
    owner_id text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    requirements_revision integer NOT NULL CHECK (requirements_revision > 0),
    requirements jsonb NOT NULL CHECK (jsonb_typeof(requirements) = 'object'
        AND requirements ? 'requirements_revision'
        AND (requirements->>'requirements_revision')::integer = requirements_revision),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    item_confirmations jsonb NOT NULL CHECK (jsonb_typeof(item_confirmations) = 'array'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (trip_id, revision),
    UNIQUE (trip_id, owner_id, revision),
    FOREIGN KEY (trip_id, owner_id) REFERENCES easytravel.trips(trip_id, owner_id)
);
CREATE TABLE easytravel.confirmed_snapshots (
    snapshot_id text PRIMARY KEY,
    trip_id text NOT NULL,
    confirmed_by text NOT NULL,
    source_working_revision integer NOT NULL,
    disclosure_hash text NOT NULL CHECK (disclosure_hash ~ '^[0-9a-f]{64}$'),
    budget jsonb NOT NULL CHECK (jsonb_typeof(budget) = 'object'),
    confirmed_at timestamptz NOT NULL,
    UNIQUE (trip_id, source_working_revision),
    UNIQUE (snapshot_id, trip_id, confirmed_by),
    FOREIGN KEY (trip_id, confirmed_by, source_working_revision)
        REFERENCES easytravel.draft_versions(trip_id, owner_id, revision)
);
-- WorkingDraft is a mutable pointer; all content lives in immutable versions.
CREATE TABLE easytravel.working_drafts (
    trip_id text PRIMARY KEY,
    owner_id text NOT NULL,
    revision integer NOT NULL,
    latest_confirmed_version_id text,
    CONSTRAINT working_version_fk FOREIGN KEY (trip_id, owner_id, revision)
        REFERENCES easytravel.draft_versions(trip_id, owner_id, revision) DEFERRABLE,
    FOREIGN KEY (latest_confirmed_version_id, trip_id, owner_id)
        REFERENCES easytravel.confirmed_snapshots(snapshot_id, trip_id, confirmed_by)
);
CREATE TABLE easytravel.change_proposals (
    proposal_id text PRIMARY KEY,
    trip_id text NOT NULL,
    owner_id text NOT NULL,
    base_working_revision integer NOT NULL,
    requirements_revision integer NOT NULL CHECK (requirements_revision > 0),
    output jsonb NOT NULL CHECK (jsonb_typeof(output) = 'object'),
    candidate jsonb NOT NULL CHECK (jsonb_typeof(candidate) = 'object'),
    level text NOT NULL CHECK (level IN ('L1', 'L2', 'L3')),
    touched_item_ids text[] NOT NULL CHECK (cardinality(touched_item_ids) > 0),
    candidate_hash text NOT NULL CHECK (candidate_hash ~ '^[0-9a-f]{64}$'),
    FOREIGN KEY (trip_id, owner_id, base_working_revision)
        REFERENCES easytravel.draft_versions(trip_id, owner_id, revision),
    UNIQUE (proposal_id, trip_id, owner_id, candidate_hash, base_working_revision, touched_item_ids)
);
CREATE TABLE easytravel.operation_receipts (
    operation_id text PRIMARY KEY,
    trip_id text NOT NULL,
    owner_id text NOT NULL,
    operation_type text NOT NULL CHECK (operation_type IN ('apply_working_change', 'confirm_plan')),
    request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    result jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (trip_id, owner_id) REFERENCES easytravel.trips(trip_id, owner_id),
    UNIQUE (operation_id, trip_id, owner_id)
);
CREATE TABLE easytravel.specific_authorizations (
    authorization_id text PRIMARY KEY,
    proposal_id text NOT NULL,
    trip_id text NOT NULL,
    granted_by text NOT NULL,
    candidate_hash text NOT NULL,
    base_working_revision integer NOT NULL,
    item_ids text[] NOT NULL,
    granted_at timestamptz NOT NULL,
    consumed_by_operation_id text,
    FOREIGN KEY (proposal_id, trip_id, granted_by, candidate_hash, base_working_revision, item_ids)
        REFERENCES easytravel.change_proposals
            (proposal_id, trip_id, owner_id, candidate_hash, base_working_revision, touched_item_ids),
    FOREIGN KEY (consumed_by_operation_id, trip_id, granted_by)
        REFERENCES easytravel.operation_receipts(operation_id, trip_id, owner_id)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX trips_owner_idx ON easytravel.trips(owner_id, trip_id);
CREATE INDEX proposals_trip_idx ON easytravel.change_proposals(trip_id);
CREATE INDEX receipts_trip_idx ON easytravel.operation_receipts(trip_id);

CREATE FUNCTION easytravel.forbid_mutation() RETURNS trigger LANGUAGE plpgsql
    SET search_path = pg_catalog AS $$
BEGIN
    RAISE EXCEPTION 'immutable_business_record' USING ERRCODE = '55000';
END $$;
CREATE TRIGGER immutable_snapshot BEFORE UPDATE OR DELETE ON easytravel.confirmed_snapshots
    FOR EACH ROW EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE TRIGGER no_truncate_snapshot BEFORE TRUNCATE ON easytravel.confirmed_snapshots
    FOR EACH STATEMENT EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE TRIGGER immutable_version BEFORE UPDATE OR DELETE ON easytravel.draft_versions
    FOR EACH ROW EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE TRIGGER no_truncate_version BEFORE TRUNCATE ON easytravel.draft_versions
    FOR EACH STATEMENT EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE TRIGGER immutable_proposal BEFORE UPDATE OR DELETE ON easytravel.change_proposals
    FOR EACH ROW EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE TRIGGER no_truncate_proposal BEFORE TRUNCATE ON easytravel.change_proposals
    FOR EACH STATEMENT EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE TRIGGER immutable_receipt BEFORE UPDATE OR DELETE ON easytravel.operation_receipts
    FOR EACH ROW EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE TRIGGER no_truncate_receipt BEFORE TRUNCATE ON easytravel.operation_receipts
    FOR EACH STATEMENT EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE FUNCTION easytravel.consume_once() RETURNS trigger LANGUAGE plpgsql
    SET search_path = pg_catalog AS $$
BEGIN
    IF OLD.consumed_by_operation_id IS NOT NULL OR NEW.consumed_by_operation_id IS NULL
       OR (to_jsonb(NEW) - 'consumed_by_operation_id') <> (to_jsonb(OLD) - 'consumed_by_operation_id') THEN
        RAISE EXCEPTION 'authorization_is_single_use' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER authorization_consumption BEFORE UPDATE ON easytravel.specific_authorizations
    FOR EACH ROW EXECUTE FUNCTION easytravel.consume_once();
CREATE TRIGGER immutable_authorization BEFORE DELETE ON easytravel.specific_authorizations
    FOR EACH ROW EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE TRIGGER no_truncate_authorization BEFORE TRUNCATE ON easytravel.specific_authorizations
    FOR EACH STATEMENT EXECUTE FUNCTION easytravel.forbid_mutation();
