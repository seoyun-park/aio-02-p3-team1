-- PRD v1.1 visitor session, itinerary group/version, and durable worker queue.
-- Keep operational status constraints from 003_operations_logging.sql unchanged.
BEGIN;

CREATE TABLE visitor_sessions (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    CHECK (expires_at > created_at),
    CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);

CREATE INDEX visitor_sessions_expires_idx
    ON visitor_sessions (expires_at)
    WHERE revoked_at IS NULL;

CREATE TABLE itinerary_groups (
    group_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    visitor_session_id UUID NOT NULL
        REFERENCES visitor_sessions (id) ON DELETE CASCADE,
    origin_stop_id TEXT NOT NULL,
    destination_stop_id TEXT NOT NULL,
    preferred_departure_time TIME NOT NULL,
    companion_type TEXT NOT NULL
        CHECK (companion_type IN ('family', 'couple', 'friends', 'solo')),
    activity_type TEXT NOT NULL
        CHECK (activity_type IN (
            'exhibition_performance', 'culture_event', 'walk_healing', 'food_cafe'
        )),
    regeneration_count SMALLINT NOT NULL DEFAULT 0
        CHECK (regeneration_count BETWEEN 0 AND 2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    CHECK (origin_stop_id <> destination_stop_id),
    CHECK (expires_at > created_at)
);

CREATE INDEX itinerary_groups_session_created_idx
    ON itinerary_groups (visitor_session_id, created_at DESC);

CREATE TABLE itinerary_executions (
    run_id UUID PRIMARY KEY REFERENCES agent_runs (run_id) ON DELETE CASCADE,
    group_id UUID NOT NULL REFERENCES itinerary_groups (group_id) ON DELETE CASCADE,
    generation_index SMALLINT NOT NULL CHECK (generation_index BETWEEN 0 AND 2),
    status TEXT NOT NULL DEFAULT 'RUNNING'
        CHECK (status IN ('RUNNING', 'SUCCESS', 'NO_RESULT', 'FAILED')),
    queue_status TEXT NOT NULL DEFAULT 'QUEUED'
        CHECK (queue_status IN ('QUEUED', 'CLAIMED', 'DONE')),
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    execution_started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    worker_id TEXT,
    worker_lease_until TIMESTAMPTZ,
    worker_attempt_count SMALLINT NOT NULL DEFAULT 0
        CHECK (worker_attempt_count >= 0),
    reason_code TEXT,
    reason_message TEXT,
    error_code TEXT,
    error_message TEXT,
    retryable BOOLEAN,
    UNIQUE (run_id, group_id),
    CHECK (completed_at IS NULL OR completed_at >= accepted_at),
    CHECK (
        (status IN ('RUNNING') AND completed_at IS NULL)
        OR (status IN ('SUCCESS', 'NO_RESULT', 'FAILED') AND completed_at IS NOT NULL)
    ),
    CHECK (
        (status = 'NO_RESULT' AND reason_code IS NOT NULL AND reason_message IS NOT NULL)
        OR status <> 'NO_RESULT'
    ),
    CHECK (
        (status = 'FAILED' AND error_code IS NOT NULL AND error_message IS NOT NULL
            AND retryable IS NOT NULL)
        OR status <> 'FAILED'
    )
);

CREATE INDEX itinerary_executions_queue_idx
    ON itinerary_executions (accepted_at, run_id)
    WHERE status = 'RUNNING' AND queue_status IN ('QUEUED', 'CLAIMED');
CREATE INDEX itinerary_executions_group_idx
    ON itinerary_executions (group_id, accepted_at DESC);
CREATE UNIQUE INDEX itinerary_executions_active_group_idx
    ON itinerary_executions (group_id)
    WHERE status = 'RUNNING';

CREATE TABLE itinerary_versions (
    itinerary_version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id UUID NOT NULL REFERENCES itinerary_groups (group_id) ON DELETE CASCADE,
    version SMALLINT NOT NULL CHECK (version BETWEEN 0 AND 2),
    execution_id UUID NOT NULL UNIQUE,
    title TEXT NOT NULL,
    itinerary_json JSONB NOT NULL CHECK (jsonb_typeof(itinerary_json) = 'object'),
    source_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(source_snapshot) = 'object'),
    validation_status TEXT NOT NULL
        CHECK (validation_status = 'POSSIBLE'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (group_id, version),
    FOREIGN KEY (execution_id, group_id)
        REFERENCES itinerary_executions (run_id, group_id) ON DELETE CASCADE
);

CREATE TABLE itinerary_stops (
    itinerary_stop_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    itinerary_version_id UUID NOT NULL
        REFERENCES itinerary_versions (itinerary_version_id) ON DELETE CASCADE,
    sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
    item_type TEXT NOT NULL CHECK (item_type IN ('FERRY', 'EVENT', 'PLACE', 'WALK')),
    title TEXT NOT NULL,
    start_at TIMESTAMPTZ,
    end_at TIMESTAMPTZ,
    reason TEXT NOT NULL,
    sources JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(sources) = 'array'),
    latitude NUMERIC(9, 6) CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
    longitude NUMERIC(10, 6) CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180),
    place_name TEXT,
    address TEXT,
    departure_stop_id TEXT,
    departure_stop_name TEXT,
    arrival_stop_id TEXT,
    arrival_stop_name TEXT,
    UNIQUE (itinerary_version_id, sequence_no),
    CHECK (end_at IS NULL OR start_at IS NULL OR end_at >= start_at),
    CHECK ((latitude IS NULL) = (longitude IS NULL)),
    CHECK (
        (item_type = 'FERRY'
            AND departure_stop_id IS NOT NULL AND departure_stop_name IS NOT NULL
            AND arrival_stop_id IS NOT NULL AND arrival_stop_name IS NOT NULL)
        OR (item_type <> 'FERRY'
            AND departure_stop_id IS NULL AND departure_stop_name IS NULL
            AND arrival_stop_id IS NULL AND arrival_stop_name IS NULL)
    )
);

CREATE INDEX itinerary_stops_version_order_idx
    ON itinerary_stops (itinerary_version_id, sequence_no);

COMMENT ON TABLE itinerary_executions IS
    'Durable user-facing execution state and worker queue; NO_RESULT is separate from operational agent_runs.status.';
COMMENT ON COLUMN itinerary_versions.execution_id IS
    'run_id of the execution that produced this version.';
COMMENT ON COLUMN itinerary_versions.itinerary_json IS
    'Immutable public itinerary snapshot, including its ordered items.';
COMMENT ON TABLE itinerary_stops IS
    'Normalized itinerary item rows. API renders this collection as itinerary.items.';

COMMIT;
