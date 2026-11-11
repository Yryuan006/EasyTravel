-- M1-03a. Explicit backfill of question descriptors, never user answers.
ALTER TABLE easytravel.api_commands DROP CONSTRAINT api_commands_method_check;
ALTER TABLE easytravel.api_commands DROP CONSTRAINT api_commands_path_check;
ALTER TABLE easytravel.api_commands DROP CONSTRAINT api_commands_response_status_check;
ALTER TABLE easytravel.api_commands ADD CONSTRAINT api_commands_route_check CHECK (
 (method='POST' AND path='/api/v1/trips' AND response_status=201) OR
 (method='PUT' AND path ~ '^/api/v1/trips/[A-Za-z0-9_-]{1,80}/requirements$' AND response_status=200) OR
 (method='POST' AND path ~ '^/api/v1/trips/[A-Za-z0-9_-]{1,80}/clarifications/[A-Za-z0-9_-]{1,80}/answers$' AND response_status=200));
CREATE TABLE easytravel.clarification_questions (
 question_id uuid PRIMARY KEY,
 trip_id text NOT NULL,
 owner_id text NOT NULL,
 intake_revision integer NOT NULL,
 field_key text NOT NULL CHECK (field_key IN ('origin','destination','dates','days','travelers','target_budget_cents','hard_limit_cents','transport','hotel','restrictions')),
 prompt text NOT NULL,
 allow_no_preference boolean NOT NULL,
 UNIQUE(trip_id, intake_revision, field_key),
 UNIQUE(question_id,trip_id,owner_id,intake_revision,field_key),
 FOREIGN KEY(trip_id,owner_id,intake_revision) REFERENCES easytravel.trip_intake_versions(trip_id,owner_id,revision)
);
CREATE TABLE easytravel.clarification_answers (
 question_id uuid PRIMARY KEY,
 trip_id text NOT NULL,
 owner_id text NOT NULL,
 intake_revision integer NOT NULL,
 field_key text NOT NULL,
 resulting_revision integer NOT NULL CHECK(resulting_revision=intake_revision+1),
 answer jsonb NOT NULL,
 command_id uuid NOT NULL UNIQUE REFERENCES easytravel.api_commands(command_id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(question_id,trip_id,owner_id,intake_revision,field_key)
  REFERENCES easytravel.clarification_questions(question_id,trip_id,owner_id,intake_revision,field_key),
 FOREIGN KEY(trip_id,owner_id,resulting_revision) REFERENCES easytravel.trip_intake_versions(trip_id,owner_id,revision)
);
-- Descriptors are preallocated per version. Only the rule-selected descriptor is answerable.
-- UUID names include the rule version and cannot collide across Trip/revision/field.
CREATE FUNCTION easytravel.prepare_clarifications() RETURNS trigger LANGUAGE plpgsql
 SET search_path=pg_catalog AS $$
BEGIN
 INSERT INTO easytravel.clarification_questions
 SELECT md5('m103a-v1:' || NEW.trip_id || ':' || NEW.revision::text || ':' || f.field_key)::uuid,
 NEW.trip_id,NEW.owner_id,NEW.revision,f.field_key,f.prompt,true
 FROM (VALUES
 ('origin','请明确出发地。'),
 ('destination','请明确目的地。'),
 ('dates','请选择具体出发和结束日期，并纠正日期与天数的冲突。'),
 ('days','请明确旅行天数（1–5 天），并与日期保持一致。'),
 ('travelers','请明确出行人数（1 或 2 人）。'),
 ('target_budget_cents','目标预算超过硬上限，请修正目标预算或修改硬上限（整数分）。'),
 ('hard_limit_cents','整趟旅行的硬预算上限是多少（全部旅客合计，整数分）？'),
 ('transport','请说明交通偏好，也可以明确没有偏好。'),
 ('hotel','请说明住宿偏好。'),
 ('restrictions','请说明旅行限制。')) AS f(field_key,prompt);
 RETURN NEW;
END $$;
CREATE TRIGGER prepare_clarifications AFTER INSERT ON easytravel.trip_intake_versions
 FOR EACH ROW EXECUTE FUNCTION easytravel.prepare_clarifications();
-- Migration-time backfill uses the same descriptor template; existing facts stay untouched.
INSERT INTO easytravel.clarification_questions
 SELECT md5('m103a-v1:' || v.trip_id || ':' || v.revision::text || ':' || f.field_key)::uuid,
 v.trip_id,v.owner_id,v.revision,f.field_key,f.prompt,true
 FROM easytravel.trip_intake_versions v CROSS JOIN (VALUES
 ('origin','请明确出发地。'),
 ('destination','请明确目的地。'),
 ('dates','请选择具体出发和结束日期，并纠正日期与天数的冲突。'),
 ('days','请明确旅行天数（1–5 天），并与日期保持一致。'),
 ('travelers','请明确出行人数（1 或 2 人）。'),
 ('target_budget_cents','目标预算超过硬上限，请修正目标预算或修改硬上限（整数分）。'),
 ('hard_limit_cents','整趟旅行的硬预算上限是多少（全部旅客合计，整数分）？'),
 ('transport','请说明交通偏好，也可以明确没有偏好。'),
 ('hotel','请说明住宿偏好。'),
 ('restrictions','请说明旅行限制。')) AS f(field_key,prompt);
CREATE TRIGGER immutable_clarification_questions BEFORE UPDATE OR DELETE ON easytravel.clarification_questions
 FOR EACH ROW EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE TRIGGER no_truncate_clarification_questions BEFORE TRUNCATE ON easytravel.clarification_questions
 FOR EACH STATEMENT EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE TRIGGER immutable_clarification_answers BEFORE UPDATE OR DELETE ON easytravel.clarification_answers
 FOR EACH ROW EXECUTE FUNCTION easytravel.forbid_mutation();
CREATE TRIGGER no_truncate_clarification_answers BEFORE TRUNCATE ON easytravel.clarification_answers
 FOR EACH STATEMENT EXECUTE FUNCTION easytravel.forbid_mutation();
