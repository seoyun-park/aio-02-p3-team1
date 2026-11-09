-- Add durable retry metadata and database outage events without rewriting
-- the shared operational logging migration (003).
BEGIN;

ALTER TABLE itinerary_executions
    ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ;

ALTER TABLE incident_logs
    ALTER COLUMN run_id DROP NOT NULL;

ALTER TABLE incident_logs
    ADD COLUMN IF NOT EXISTS monitor_event_id UUID;

CREATE UNIQUE INDEX IF NOT EXISTS incident_logs_monitor_event_id_uidx
    ON incident_logs (monitor_event_id)
    WHERE monitor_event_id IS NOT NULL;

ALTER TABLE recovery_logs
    ALTER COLUMN run_id DROP NOT NULL;

-- Keep health-monitor recovery rows connected to an incident even when there
-- is no recommendation run_id. The existing composite FK remains for runs.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'recovery_logs_incident_id_fkey'
           AND conrelid = 'recovery_logs'::regclass
    ) THEN
        ALTER TABLE recovery_logs
            ADD CONSTRAINT recovery_logs_incident_id_fkey
            FOREIGN KEY (incident_id) REFERENCES incident_logs (incident_id)
            ON DELETE CASCADE;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS itinerary_executions_retry_queue_idx
    ON itinerary_executions (next_retry_at, accepted_at, run_id)
    WHERE status = 'RUNNING' AND queue_status = 'QUEUED';

COMMENT ON COLUMN incident_logs.monitor_event_id IS
    'Idempotency key for an outage detected by the independent database health monitor.';
COMMENT ON COLUMN incident_logs.run_id IS
    'NULL for infrastructure incidents that are not tied to a recommendation run.';
COMMENT ON COLUMN recovery_logs.run_id IS
    'NULL for recovery actions on infrastructure incidents outside a recommendation run.';

COMMIT;
