-- Dedicated Architecture Spike test tables. Not production migrations.
CREATE TABLE IF NOT EXISTS working_drafts (
    trip_id text PRIMARY KEY,
    owner_id text NOT NULL CHECK (owner_id='demo_owner'),
    revision integer NOT NULL CHECK (revision > 0),
    requirements_revision integer NOT NULL CHECK (requirements_revision > 0),
    requirements jsonb NOT NULL,
    payload jsonb NOT NULL,
    item_confirmations jsonb NOT NULL,
    latest_confirmed_version_id text
);
CREATE TABLE IF NOT EXISTS confirmed_snapshots (
    snapshot_id text PRIMARY KEY,
    trip_id text NOT NULL REFERENCES working_drafts(trip_id),
    source_working_revision integer NOT NULL,
    data jsonb NOT NULL,
    UNIQUE(trip_id, source_working_revision),
    UNIQUE(snapshot_id, trip_id)
);
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='spike03_latest_snapshot_fk'
                   AND conrelid='working_drafts'::regclass) THEN
        ALTER TABLE working_drafts ADD CONSTRAINT spike03_latest_snapshot_fk
            FOREIGN KEY(latest_confirmed_version_id, trip_id)
            REFERENCES confirmed_snapshots(snapshot_id, trip_id) DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;
CREATE OR REPLACE FUNCTION forbid_snapshot_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'confirmed_snapshots_are_immutable' USING ERRCODE='55000';
END $$;
CREATE OR REPLACE TRIGGER immutable_snapshot BEFORE UPDATE OR DELETE ON confirmed_snapshots
    FOR EACH ROW EXECUTE FUNCTION forbid_snapshot_mutation();
CREATE TABLE IF NOT EXISTS change_proposals (
    proposal_id text PRIMARY KEY,
    trip_id text NOT NULL REFERENCES working_drafts(trip_id),
    data jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS specific_authorizations (
    authorization_id text PRIMARY KEY,
    proposal_id text NOT NULL REFERENCES change_proposals(proposal_id),
    data jsonb NOT NULL,
    consumed_by_operation_id text
);
CREATE TABLE IF NOT EXISTS operation_receipts (
    operation_id text PRIMARY KEY,
    trip_id text NOT NULL REFERENCES working_drafts(trip_id),
    operation_type text NOT NULL CHECK (operation_type IN ('apply_working_change','confirm_plan')),
    request_hash text NOT NULL,
    result jsonb NOT NULL
);
