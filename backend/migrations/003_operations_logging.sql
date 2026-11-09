-- Operational logs shared by LangGraph, the backend, and the admin dashboard.
-- Never persist prompts, response bodies, credentials, or personal data here.
BEGIN;

CREATE TABLE agent_runs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status VARCHAR(20) NOT NULL DEFAULT 'RUNNING'
        CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    error_source VARCHAR(20)
        CHECK (error_source IS NULL OR error_source IN (
            'LANGGRAPH', 'LLM', 'EXTERNAL_API', 'DATABASE', 'UNKNOWN'
        )),
    error_code VARCHAR(100),
    failed_step_name VARCHAR(100),
    CHECK (
        (status = 'RUNNING' AND finished_at IS NULL AND duration_ms IS NULL)
        OR (status <> 'RUNNING' AND finished_at IS NOT NULL AND duration_ms IS NOT NULL)
    ),
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE INDEX agent_runs_started_idx ON agent_runs (started_at DESC);
CREATE INDEX agent_runs_status_started_idx ON agent_runs (status, started_at DESC);
CREATE INDEX agent_runs_error_source_idx ON agent_runs (error_source, started_at DESC)
    WHERE error_source IS NOT NULL;

CREATE TABLE agent_step_logs (
    step_log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES agent_runs (run_id) ON DELETE CASCADE,
    step_order INTEGER NOT NULL CHECK (step_order > 0),
    agent_name VARCHAR(100) NOT NULL,
    step_name VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'RUNNING'
        CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    error_source VARCHAR(20)
        CHECK (error_source IS NULL OR error_source IN (
            'LANGGRAPH', 'LLM', 'EXTERNAL_API', 'DATABASE', 'UNKNOWN'
        )),
    error_code VARCHAR(100),
    CHECK (
        (status = 'RUNNING' AND finished_at IS NULL AND duration_ms IS NULL)
        OR (status <> 'RUNNING' AND finished_at IS NOT NULL AND duration_ms IS NOT NULL)
    ),
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE INDEX agent_step_logs_run_order_idx ON agent_step_logs (run_id, step_order, started_at);
CREATE INDEX agent_step_logs_status_idx ON agent_step_logs (status, started_at DESC);

CREATE TABLE api_call_logs (
    api_call_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES agent_runs (run_id) ON DELETE CASCADE,
    provider VARCHAR(100) NOT NULL,
    api_type VARCHAR(30) NOT NULL
        CHECK (api_type IN ('LLM', 'CULTURE', 'MAPS', 'OTHER')),
    http_status INTEGER CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
    latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
    success BOOLEAN NOT NULL,
    called_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    error_source VARCHAR(20)
        CHECK (error_source IS NULL OR error_source IN (
            'LANGGRAPH', 'LLM', 'EXTERNAL_API', 'DATABASE', 'UNKNOWN'
        )),
    error_code VARCHAR(100),
    UNIQUE (api_call_id, run_id)
);

CREATE INDEX api_call_logs_run_called_idx ON api_call_logs (run_id, called_at DESC);
CREATE INDEX api_call_logs_called_idx ON api_call_logs (called_at DESC);
CREATE INDEX api_call_logs_provider_called_idx ON api_call_logs (provider, called_at DESC);
CREATE INDEX api_call_logs_type_success_idx ON api_call_logs (api_type, success, called_at DESC);

CREATE TABLE handoff_logs (
    handoff_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES agent_runs (run_id) ON DELETE CASCADE,
    from_agent VARCHAR(100) NOT NULL,
    to_agent VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'RUNNING'
        CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    error_source VARCHAR(20)
        CHECK (error_source IS NULL OR error_source IN (
            'LANGGRAPH', 'LLM', 'EXTERNAL_API', 'DATABASE', 'UNKNOWN'
        )),
    error_code VARCHAR(100),
    CHECK (
        (status = 'RUNNING' AND finished_at IS NULL AND duration_ms IS NULL)
        OR (status <> 'RUNNING' AND finished_at IS NOT NULL AND duration_ms IS NOT NULL)
    ),
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE INDEX handoff_logs_run_started_idx ON handoff_logs (run_id, started_at);

CREATE TABLE incident_logs (
    incident_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES agent_runs (run_id) ON DELETE CASCADE,
    api_call_id UUID,
    status VARCHAR(20) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'RECOVERING', 'RESOLVED')),
    severity VARCHAR(20) NOT NULL
        CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    error_source VARCHAR(20) NOT NULL
        CHECK (error_source IN (
            'LANGGRAPH', 'LLM', 'EXTERNAL_API', 'DATABASE', 'UNKNOWN'
        )),
    error_code VARCHAR(100) NOT NULL,
    error_message TEXT,
    failed_step_name VARCHAR(100),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    UNIQUE (incident_id, run_id),
    FOREIGN KEY (api_call_id, run_id)
        REFERENCES api_call_logs (api_call_id, run_id),
    CHECK (
        (status = 'RESOLVED' AND resolved_at IS NOT NULL)
        OR (status <> 'RESOLVED' AND resolved_at IS NULL)
    ),
    CHECK (resolved_at IS NULL OR resolved_at >= occurred_at)
);

CREATE INDEX incident_logs_run_occurred_idx ON incident_logs (run_id, occurred_at DESC);
CREATE INDEX incident_logs_status_source_idx ON incident_logs (status, error_source, occurred_at DESC);
CREATE INDEX incident_logs_source_occurred_idx ON incident_logs (error_source, occurred_at DESC);

CREATE TABLE recovery_logs (
    recovery_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id UUID NOT NULL,
    run_id UUID NOT NULL REFERENCES agent_runs (run_id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    recovery_action VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'RUNNING'
        CHECK (status IN ('RUNNING', 'RECOVERED', 'FAILED')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    UNIQUE (incident_id, attempt_number),
    FOREIGN KEY (incident_id, run_id)
        REFERENCES incident_logs (incident_id, run_id) ON DELETE CASCADE,
    CHECK (
        (status = 'RUNNING' AND finished_at IS NULL AND duration_ms IS NULL)
        OR (status <> 'RUNNING' AND finished_at IS NOT NULL AND duration_ms IS NOT NULL)
    ),
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE INDEX recovery_logs_run_started_idx ON recovery_logs (run_id, started_at DESC);
CREATE INDEX recovery_logs_status_finished_idx ON recovery_logs (status, finished_at DESC);

COMMENT ON COLUMN api_call_logs.http_status IS
    'NULL for timeouts or network failures where no HTTP response was received.';
COMMENT ON COLUMN incident_logs.error_message IS
    'Sanitized, bounded summary only; never store request URLs, credentials, prompts, payloads, or personal data.';
COMMENT ON COLUMN api_call_logs.api_type IS
    'MAPS is reserved for a confirmed maps integration; no Kakao Maps calls are currently configured.';

COMMIT;
