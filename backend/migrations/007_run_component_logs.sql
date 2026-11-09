-- Per-recommendation component status snapshots for the operations dashboard.
-- Existing agent/API logs remain the detailed event history.
BEGIN;

CREATE TABLE run_component_logs (
    run_id UUID NOT NULL REFERENCES agent_runs (run_id) ON DELETE CASCADE,
    component_name VARCHAR(40) NOT NULL
        CHECK (component_name IN (
            'POSTGRESQL',
            'OPERATIONAL_LOG_SCHEMA',
            'LANGGRAPH',
            'LLM_API',
            'SEOUL_CULTURAL_EVENTS'
        )),
    status VARCHAR(20) NOT NULL
        CHECK (status IN ('UP', 'DOWN', 'DEGRADED', 'RUNNING', 'NOT_RUN', 'UNKNOWN')),
    started_at TIMESTAMPTZ,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    latency_ms INTEGER CHECK (latency_ms IS NULL OR latency_ms >= 0),
    reason_code VARCHAR(100),
    PRIMARY KEY (run_id, component_name),
    CHECK (started_at IS NULL OR checked_at >= started_at)
);

CREATE INDEX run_component_logs_checked_idx
    ON run_component_logs (checked_at DESC, component_name, status);

-- Preserve older runs in the new per-run view without inventing API outcomes.
INSERT INTO run_component_logs (
    run_id, component_name, status, started_at, checked_at, reason_code
)
SELECT run_id, 'POSTGRESQL', 'UP', started_at,
       now(), 'RUN_RECORD_PERSISTED'
  FROM agent_runs;

INSERT INTO run_component_logs (
    run_id, component_name, status, started_at, checked_at, reason_code
)
SELECT run_id, 'OPERATIONAL_LOG_SCHEMA', 'UP', started_at,
       now(), 'LOG_RECORD_PERSISTED'
  FROM agent_runs;

INSERT INTO run_component_logs (
    run_id, component_name, status, started_at, checked_at, reason_code
)
SELECT runs.run_id,
       'LANGGRAPH',
       CASE
           WHEN coalesce(steps.total, 0) = 0 THEN 'UNKNOWN'
           WHEN steps.failed > 0 AND steps.succeeded = 0 THEN 'DOWN'
           WHEN steps.failed > 0 OR steps.running > 0 THEN 'DEGRADED'
           ELSE 'UP'
       END,
       coalesce(steps.first_started_at, runs.started_at),
       now(),
       CASE WHEN coalesce(steps.total, 0) = 0
            THEN 'HISTORICAL_STEP_STATUS_UNAVAILABLE'
            WHEN steps.failed > 0 THEN 'HISTORICAL_STEP_FAILURE'
            ELSE 'HISTORICAL_STEPS_RECORDED' END
  FROM agent_runs AS runs
  LEFT JOIN (
      SELECT run_id,
             count(*)::integer AS total,
             count(*) FILTER (WHERE status = 'FAILED')::integer AS failed,
             count(*) FILTER (WHERE status = 'SUCCESS')::integer AS succeeded,
             count(*) FILTER (WHERE status = 'RUNNING')::integer AS running,
             min(started_at) AS first_started_at
        FROM agent_step_logs
       GROUP BY run_id
  ) AS steps USING (run_id);

INSERT INTO run_component_logs (
    run_id, component_name, status, started_at, checked_at, latency_ms, reason_code
)
SELECT runs.run_id,
       components.component_name,
       CASE
           WHEN coalesce(calls.total, 0) = 0 THEN 'UNKNOWN'
           WHEN calls.failed > 0 AND calls.succeeded = 0 THEN 'DOWN'
           WHEN calls.failed > 0 THEN 'DEGRADED'
           ELSE 'UP'
       END,
       coalesce(calls.first_called_at, runs.started_at),
       now(),
       calls.total_latency_ms,
       CASE WHEN coalesce(calls.total, 0) = 0
            THEN 'HISTORICAL_API_CALL_STATUS_UNAVAILABLE'
            WHEN calls.failed > 0 THEN 'HISTORICAL_API_CALL_FAILURE'
            ELSE 'HISTORICAL_API_CALLS_SUCCEEDED' END
  FROM agent_runs AS runs
 CROSS JOIN (VALUES ('LLM_API', 'LLM'), ('SEOUL_CULTURAL_EVENTS', 'CULTURE'))
      AS components(component_name, api_type)
  LEFT JOIN (
      SELECT run_id, api_type,
             count(*)::integer AS total,
             count(*) FILTER (WHERE success)::integer AS succeeded,
             count(*) FILTER (WHERE NOT success)::integer AS failed,
             coalesce(sum(latency_ms), 0)::integer AS total_latency_ms,
             min(called_at) AS first_called_at
        FROM api_call_logs
       GROUP BY run_id, api_type
  ) AS calls ON calls.run_id = runs.run_id
            AND calls.api_type = components.api_type;

COMMIT;
