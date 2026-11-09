-- Persist sanitized LangGraph Critic decisions for each itinerary execution.
BEGIN;

CREATE TABLE itinerary_critic_reviews (
    critic_review_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES agent_runs (run_id) ON DELETE CASCADE,
    revision_number SMALLINT NOT NULL CHECK (revision_number >= 0),
    decision VARCHAR(20) NOT NULL CHECK (decision IN ('pass', 'revise', 'stop')),
    issues JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(issues) = 'array'),
    revision_instructions TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, revision_number)
);

CREATE INDEX itinerary_critic_reviews_run_idx
    ON itinerary_critic_reviews (run_id, revision_number);

COMMENT ON TABLE itinerary_critic_reviews IS
    'Sanitized Critic decisions only; prompts, raw model responses, credentials, and personal data are not stored.';

COMMIT;
