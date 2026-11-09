-- Dedicated experimental tables, not formal application migrations.
CREATE TABLE IF NOT EXISTS proposals (
    thread_id text PRIMARY KEY,
    proposal_id text NOT NULL UNIQUE,
    operation_id text NOT NULL UNIQUE,
    owner_id text NOT NULL CHECK (owner_id = 'demo_owner'),
    graph_version text NOT NULL,
    request_hash text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS decisions (
    proposal_id text PRIMARY KEY REFERENCES proposals(proposal_id),
    decision_id text NOT NULL UNIQUE,
    owner_id text NOT NULL CHECK (owner_id = 'demo_owner'),
    choice text NOT NULL CHECK (choice IN ('approve', 'reject')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS decision_requests (
    request_id text PRIMARY KEY,
    request_hash text NOT NULL,
    decision_id text NOT NULL REFERENCES decisions(decision_id)
);
CREATE TABLE IF NOT EXISTS results (
    operation_id text PRIMARY KEY REFERENCES proposals(operation_id),
    result_id text NOT NULL UNIQUE,
    proposal_id text NOT NULL UNIQUE REFERENCES proposals(proposal_id),
    payload jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts (
    operation_id text PRIMARY KEY REFERENCES results(operation_id),
    request_hash text NOT NULL,
    result_id text NOT NULL REFERENCES results(result_id)
);
