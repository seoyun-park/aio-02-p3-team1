"""Persist recovery attempts and safely requeue recommendation executions."""
from __future__ import annotations

from uuid import UUID

from psycopg import Connection

from app.services.operation_logs_service import (
    create_incident,
    finish_recovery,
    start_recovery,
)


def start_run_recovery(
    connection: Connection,
    run_id: UUID,
    error_source: str,
    error_code: str,
    error_message: str,
    failed_step_name: str,
    recovery_action: str,
) -> UUID:
    """Open or reuse an incident and start its next bounded recovery attempt."""
    incident_row = connection.execute(
        """SELECT incident_id
             FROM incident_logs
            WHERE run_id = %s AND error_code = %s
              AND status IN ('OPEN', 'RECOVERING')
            ORDER BY occurred_at DESC
            LIMIT 1""",
        (run_id, error_code),
    ).fetchone()
    if incident_row is None:
        incident_id = create_incident(
            connection,
            run_id,
            severity='MEDIUM' if error_source in {'LLM', 'EXTERNAL_API'} else 'HIGH',
            error_source=error_source,
            error_code=error_code,
            error_message=error_message,
            failed_step_name=failed_step_name,
        )
    else:
        incident_id = incident_row[0]

    running_rows = connection.execute(
        """SELECT recovery_id
             FROM recovery_logs
            WHERE incident_id = %s AND status = 'RUNNING'
            ORDER BY attempt_number""",
        (incident_id,),
    ).fetchall()
    for (recovery_id,) in running_rows:
        finish_recovery(connection, recovery_id, 'FAILED')

    attempt_row = connection.execute(
        """SELECT COALESCE(MAX(attempt_number), 0) + 1
             FROM recovery_logs
            WHERE incident_id = %s""",
        (incident_id,),
    ).fetchone()
    start_recovery(
        connection,
        incident_id,
        run_id,
        int(attempt_row[0]),
        recovery_action,
    )
    return incident_id


def record_terminal_run_failure(
    connection: Connection,
    run_id: UUID,
    error_source: str,
    error_code: str,
    error_message: str,
    failed_step_name: str,
    severity: str = 'HIGH',
) -> UUID:
    """Ensure a terminal failure has an incident row and no open recovery."""
    incident_row = connection.execute(
        """SELECT incident_id
             FROM incident_logs
            WHERE run_id = %s AND error_code = %s
              AND status IN ('OPEN', 'RECOVERING')
            ORDER BY occurred_at DESC
            LIMIT 1""",
        (run_id, error_code),
    ).fetchone()
    incident_id = (
        incident_row[0]
        if incident_row is not None
        else create_incident(
            connection,
            run_id,
            severity=severity,
            error_source=error_source,
            error_code=error_code,
            error_message=error_message,
            failed_step_name=failed_step_name,
        )
    )
    running_rows = connection.execute(
        """SELECT recovery_id FROM recovery_logs
            WHERE run_id = %s AND status = 'RUNNING'""",
        (run_id,),
    ).fetchall()
    for (recovery_id,) in running_rows:
        finish_recovery(connection, recovery_id, 'FAILED')
    return incident_id


def finish_run_recoveries(
    connection: Connection,
    run_id: UUID,
    status: str,
) -> None:
    """Finish every active recovery attempt attached to a recommendation run."""
    rows = connection.execute(
        """SELECT recovery_id
             FROM recovery_logs
            WHERE run_id = %s AND status = 'RUNNING'
            ORDER BY started_at""",
        (run_id,),
    ).fetchall()
    for (recovery_id,) in rows:
        finish_recovery(connection, recovery_id, status)


def schedule_execution_retry(
    connection: Connection,
    run_id: UUID,
    worker_id: str,
    delay_seconds: int,
) -> bool:
    """Return a still-running claimed execution to the durable queue."""
    row = connection.execute(
        """UPDATE itinerary_executions
              SET queue_status = 'QUEUED',
                  next_retry_at = now() + (%s * interval '1 second'),
                  worker_id = NULL,
                  worker_lease_until = NULL
            WHERE run_id = %s AND status = 'RUNNING'
              AND queue_status = 'CLAIMED' AND worker_id = %s
         RETURNING run_id""",
        (delay_seconds, run_id, worker_id),
    ).fetchone()
    return row is not None


def resume_agent_run(connection: Connection, run_id: UUID) -> None:
    """Reopen a run whose worker died after persisting a failed attempt."""
    connection.execute(
        """UPDATE agent_runs
              SET status = 'RUNNING', finished_at = NULL, duration_ms = NULL,
                  error_source = NULL, error_code = NULL, failed_step_name = NULL
            WHERE run_id = %s AND status = 'FAILED'""",
        (run_id,),
    )


def close_abandoned_step_logs(connection: Connection, run_id: UUID) -> None:
    """Mark steps/handoffs left open by a crashed worker as interrupted."""
    connection.execute(
        """UPDATE agent_step_logs
              SET status = 'FAILED', finished_at = now(),
                  duration_ms = GREATEST(0, floor(extract(epoch FROM (now() - started_at)) * 1000)::integer),
                  error_source = 'UNKNOWN', error_code = 'WORKER_LEASE_EXPIRED'
            WHERE run_id = %s AND status = 'RUNNING'""",
        (run_id,),
    )
    connection.execute(
        """UPDATE handoff_logs
              SET status = 'FAILED', finished_at = now(),
                  duration_ms = GREATEST(0, floor(extract(epoch FROM (now() - started_at)) * 1000)::integer),
                  error_source = 'UNKNOWN', error_code = 'WORKER_LEASE_EXPIRED'
            WHERE run_id = %s AND status = 'RUNNING'""",
        (run_id,),
    )
