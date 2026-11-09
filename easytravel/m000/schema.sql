-- Integration bindings only; PlanService owns proposals, grants and receipts.
CREATE TABLE IF NOT EXISTS integration_runs (
    thread_id text PRIMARY KEY,
    trip_id text NOT NULL REFERENCES working_drafts(trip_id),
    proposal_id text NOT NULL UNIQUE REFERENCES change_proposals(proposal_id),
    operation_id text NOT NULL UNIQUE,
    decision_id text UNIQUE,
    request_id text UNIQUE,
    choice text CHECK (choice IN ('approve','reject')),
    authorization_id text UNIQUE REFERENCES specific_authorizations(authorization_id),
    CHECK ((decision_id IS NULL AND request_id IS NULL AND choice IS NULL AND authorization_id IS NULL)
        OR (decision_id IS NOT NULL AND request_id IS NOT NULL AND choice='reject' AND authorization_id IS NULL)
        OR (decision_id IS NOT NULL AND request_id IS NOT NULL AND choice='approve' AND authorization_id IS NOT NULL))
);
