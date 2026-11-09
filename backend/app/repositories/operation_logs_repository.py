from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import Connection


AGENT_RUN_COLUMNS = (
    'run_id', 'status', 'started_at', 'finished_at', 'duration_ms',
    'error_source', 'error_code', 'failed_step_name',
)
AGENT_STEP_COLUMNS = (
    'step_log_id', 'run_id', 'step_order', 'agent_name', 'step_name', 'status',
    'started_at', 'finished_at', 'duration_ms', 'error_source', 'error_code',
)
API_CALL_COLUMNS = (
    'api_call_id', 'run_id', 'provider', 'api_type', 'http_status', 'latency_ms',
    'success', 'called_at', 'error_source', 'error_code',
)
HANDOFF_COLUMNS = (
    'handoff_id', 'run_id', 'from_agent', 'to_agent', 'status', 'started_at',
    'finished_at', 'duration_ms', 'error_source', 'error_code',
)
INCIDENT_COLUMNS = (
    'incident_id', 'run_id', 'api_call_id', 'status', 'severity', 'error_source',
    'error_code', 'error_message', 'failed_step_name', 'occurred_at', 'resolved_at',
)
RECOVERY_COLUMNS = (
    'recovery_id', 'incident_id', 'run_id', 'attempt_number', 'recovery_action',
    'status', 'started_at', 'finished_at', 'duration_ms',
)


def map_row(row: tuple[Any, ...] | None, columns: tuple[str, ...]) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(zip(columns, row, strict=True))


def map_rows(rows: list[tuple[Any, ...]], columns: tuple[str, ...]) -> list[dict[str, Any]]:
    return [map_row(row, columns) for row in rows if row is not None]


def create_agent_run(connection: Connection, run_id: UUID | None = None) -> UUID:
    if run_id is None:
        row = connection.execute(
            "INSERT INTO agent_runs DEFAULT VALUES RETURNING run_id"
        ).fetchone()
        return row[0]

    row = connection.execute(
        "INSERT INTO agent_runs (run_id) VALUES (%s) RETURNING run_id",
        (run_id,),
    ).fetchone()
    return row[0]


def create_run_component_logs(connection: Connection, run_id: UUID) -> None:
    initial_statuses = (
        ('POSTGRESQL', 'UP', 'RUN_RECORD_CREATED'),
        ('OPERATIONAL_LOG_SCHEMA', 'UP', 'LOG_SCHEMA_WRITE_SUCCEEDED'),
        ('LANGGRAPH', 'NOT_RUN', 'WAITING_FOR_WORKER'),
        ('LLM_API', 'NOT_RUN', 'WAITING_FOR_GRAPH'),
        ('SEOUL_CULTURAL_EVENTS', 'NOT_RUN', 'WAITING_FOR_WORKER'),
    )
    for component_name, status, reason_code in initial_statuses:
        connection.execute(
            """INSERT INTO run_component_logs (
                       run_id, component_name, status, reason_code
                   ) VALUES (%s, %s, %s, %s)""",
            (run_id, component_name, status, reason_code),
        )


def update_run_component_log(
    connection: Connection,
    run_id: UUID,
    component_name: str,
    status: str,
    reason_code: str,
    latency_ms: int | None = None,
    started_at: Any | None = None,
) -> None:
    connection.execute(
        """UPDATE run_component_logs
              SET status = %s,
                  reason_code = %s,
                  latency_ms = %s,
                  started_at = coalesce(%s, started_at),
                  checked_at = GREATEST(
                      statement_timestamp(),
                      coalesce(%s, statement_timestamp())
                  )
            WHERE run_id = %s AND component_name = %s""",
        (status, reason_code, latency_ms, started_at, started_at, run_id, component_name),
    )


def finish_agent_run(
    connection: Connection,
    run_id: UUID,
    status: str,
    error_source: str | None,
    error_code: str | None,
    failed_step_name: str | None,
) -> dict[str, Any] | None:
    row = connection.execute(
        """UPDATE agent_runs
              SET status = %s,
                  finished_at = now(),
                  duration_ms = GREATEST(0, floor(extract(epoch FROM (now() - started_at)) * 1000)::integer),
                  error_source = %s,
                  error_code = %s,
                  failed_step_name = %s
            WHERE run_id = %s AND status = 'RUNNING'
         RETURNING run_id, status, started_at, finished_at, duration_ms,
                   error_source, error_code, failed_step_name""",
        (status, error_source, error_code, failed_step_name, run_id),
    ).fetchone()
    return map_row(row, AGENT_RUN_COLUMNS)


def create_agent_step_log(
    connection: Connection,
    run_id: UUID,
    step_order: int,
    agent_name: str,
    step_name: str,
) -> UUID:
    row = connection.execute(
        """INSERT INTO agent_step_logs (
                   run_id, step_order, agent_name, step_name
               ) VALUES (%s, %s, %s, %s)
            RETURNING step_log_id""",
        (run_id, step_order, agent_name, step_name),
    ).fetchone()
    return row[0]


def finish_agent_step_log(
    connection: Connection,
    step_log_id: UUID,
    status: str,
    error_source: str | None,
    error_code: str | None,
) -> dict[str, Any] | None:
    row = connection.execute(
        """UPDATE agent_step_logs
              SET status = %s,
                  finished_at = now(),
                  duration_ms = GREATEST(0, floor(extract(epoch FROM (now() - started_at)) * 1000)::integer),
                  error_source = %s,
                  error_code = %s
            WHERE step_log_id = %s AND status = 'RUNNING'
         RETURNING step_log_id, run_id, step_order, agent_name, step_name, status,
                   started_at, finished_at, duration_ms, error_source, error_code""",
        (status, error_source, error_code, step_log_id),
    ).fetchone()
    return map_row(row, AGENT_STEP_COLUMNS)


def create_api_call_log(
    connection: Connection,
    run_id: UUID,
    provider: str,
    api_type: str,
    http_status: int | None,
    latency_ms: int,
    success: bool,
    error_source: str | None,
    error_code: str | None,
) -> UUID:
    row = connection.execute(
        """INSERT INTO api_call_logs (
                   run_id, provider, api_type, http_status, latency_ms, success,
                   error_source, error_code
               ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING api_call_id""",
        (run_id, provider, api_type, http_status, latency_ms, success, error_source, error_code),
    ).fetchone()
    return row[0]


def create_handoff_log(
    connection: Connection,
    run_id: UUID,
    from_agent: str,
    to_agent: str,
) -> UUID:
    row = connection.execute(
        """INSERT INTO handoff_logs (run_id, from_agent, to_agent)
             VALUES (%s, %s, %s)
          RETURNING handoff_id""",
        (run_id, from_agent, to_agent),
    ).fetchone()
    return row[0]


def finish_handoff_log(
    connection: Connection,
    handoff_id: UUID,
    status: str,
    error_source: str | None,
    error_code: str | None,
) -> dict[str, Any] | None:
    row = connection.execute(
        """UPDATE handoff_logs
              SET status = %s,
                  finished_at = now(),
                  duration_ms = GREATEST(0, floor(extract(epoch FROM (now() - started_at)) * 1000)::integer),
                  error_source = %s,
                  error_code = %s
            WHERE handoff_id = %s AND status = 'RUNNING'
         RETURNING handoff_id, run_id, from_agent, to_agent, status, started_at,
                   finished_at, duration_ms, error_source, error_code""",
        (status, error_source, error_code, handoff_id),
    ).fetchone()
    return map_row(row, HANDOFF_COLUMNS)


def create_incident_log(
    connection: Connection,
    run_id: UUID,
    api_call_id: UUID | None,
    severity: str,
    error_source: str,
    error_code: str,
    error_message: str | None,
    failed_step_name: str | None,
) -> UUID:
    row = connection.execute(
        """INSERT INTO incident_logs (
                   run_id, api_call_id, severity, error_source, error_code,
                   error_message, failed_step_name
               ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING incident_id""",
        (
            run_id, api_call_id, severity, error_source, error_code,
            error_message, failed_step_name,
        ),
    ).fetchone()
    return row[0]


def start_recovery_log(
    connection: Connection,
    incident_id: UUID,
    run_id: UUID,
    attempt_number: int,
    recovery_action: str,
) -> UUID | None:
    incident = connection.execute(
        """UPDATE incident_logs SET status = 'RECOVERING'
            WHERE incident_id = %s AND run_id = %s AND status IN ('OPEN', 'RECOVERING')
         RETURNING incident_id""",
        (incident_id, run_id),
    ).fetchone()
    if incident is None:
        return None
    row = connection.execute(
        """INSERT INTO recovery_logs (
                   incident_id, run_id, attempt_number, recovery_action
               ) VALUES (%s, %s, %s, %s)
            RETURNING recovery_id""",
        (incident_id, run_id, attempt_number, recovery_action),
    ).fetchone()
    return row[0]


def finish_recovery_log(
    connection: Connection,
    recovery_id: UUID,
    status: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """UPDATE recovery_logs
              SET status = %s,
                  finished_at = now(),
                  duration_ms = GREATEST(0, floor(extract(epoch FROM (now() - started_at)) * 1000)::integer)
            WHERE recovery_id = %s AND status = 'RUNNING'
         RETURNING recovery_id, incident_id, run_id, attempt_number, recovery_action,
                   status, started_at, finished_at, duration_ms""",
        (status, recovery_id),
    ).fetchone()
    recovery = map_row(row, RECOVERY_COLUMNS)
    if recovery is not None and status == 'RECOVERED':
        connection.execute(
            """UPDATE incident_logs SET status = 'RESOLVED', resolved_at = now()
                WHERE incident_id = %s AND run_id = %s""",
            (recovery['incident_id'], recovery['run_id']),
        )
    elif recovery is not None and status == 'FAILED':
        connection.execute(
            """UPDATE incident_logs
                  SET status = 'OPEN'
                WHERE incident_id = %s AND run_id = %s
                  AND status = 'RECOVERING'
                  AND NOT EXISTS (
                      SELECT 1 FROM recovery_logs
                       WHERE incident_id = %s AND status = 'RUNNING'
                  )""",
            (
                recovery['incident_id'],
                recovery['run_id'],
                recovery['incident_id'],
            ),
        )
    return recovery


def get_dashboard_run_counts(connection: Connection) -> tuple[int, int, int, int, int | None]:
    row = connection.execute(
        """SELECT count(*)::integer,
                  count(*) FILTER (WHERE status = 'SUCCESS')::integer,
                  count(*) FILTER (WHERE status = 'FAILED')::integer,
                  (SELECT count(DISTINCT run_id)::integer
                     FROM recovery_logs WHERE status = 'RECOVERED'),
                  round(avg(duration_ms))::integer
             FROM agent_runs"""
    ).fetchone()
    return tuple(row)


def get_failure_counts_by_source(connection: Connection) -> list[tuple[str, int]]:
    return connection.execute(
        """SELECT error_source, count(*)::integer
             FROM incident_logs
            GROUP BY error_source"""
    ).fetchall()


def list_run_daily_counts(
    connection: Connection,
    started_after: datetime,
    now: datetime,
) -> list[tuple[Any, int, int]]:
    return connection.execute(
        """SELECT (finished_at AT TIME ZONE 'UTC')::date,
                  count(*) FILTER (WHERE status = 'SUCCESS')::integer,
                  count(*) FILTER (WHERE status = 'FAILED')::integer
             FROM agent_runs
            WHERE finished_at >= %s AND finished_at < %s
            GROUP BY 1 ORDER BY 1""",
        (started_after, now),
    ).fetchall()


def list_recovery_daily_counts(
    connection: Connection,
    started_after: datetime,
    now: datetime,
) -> list[tuple[Any, int]]:
    return connection.execute(
        """SELECT (finished_at AT TIME ZONE 'UTC')::date, count(DISTINCT run_id)::integer
             FROM recovery_logs
            WHERE status = 'RECOVERED' AND finished_at >= %s AND finished_at < %s
            GROUP BY 1 ORDER BY 1""",
        (started_after, now),
    ).fetchall()


def list_agent_runs(
    connection: Connection,
    limit: int,
    offset: int,
    status: str | None,
    error_source: str | None,
) -> tuple[int, list[dict[str, Any]]]:
    where_clauses: list[str] = []
    parameters: list[Any] = []
    if status is not None:
        where_clauses.append('status = %s')
        parameters.append(status)
    if error_source is not None:
        where_clauses.append('error_source = %s')
        parameters.append(error_source)
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ''
    total = connection.execute(
        f'SELECT count(*) FROM agent_runs {where_sql}', parameters
    ).fetchone()[0]
    rows = connection.execute(
        f"""SELECT run_id, status, started_at, finished_at, duration_ms,
                    error_source, error_code, failed_step_name
               FROM agent_runs {where_sql}
              ORDER BY started_at DESC, run_id DESC
              LIMIT %s OFFSET %s""",
        [*parameters, limit, offset],
    ).fetchall()
    return int(total), map_rows(rows, AGENT_RUN_COLUMNS)


def get_agent_run(connection: Connection, run_id: UUID) -> dict[str, Any] | None:
    row = connection.execute(
        """SELECT run_id, status, started_at, finished_at, duration_ms,
                  error_source, error_code, failed_step_name
             FROM agent_runs WHERE run_id = %s""",
        (run_id,),
    ).fetchone()
    return map_row(row, AGENT_RUN_COLUMNS)


def list_agent_steps(connection: Connection, run_id: UUID) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT step_log_id, run_id, step_order, agent_name, step_name, status,
                  started_at, finished_at, duration_ms, error_source, error_code
             FROM agent_step_logs WHERE run_id = %s
            ORDER BY step_order, started_at, step_log_id""",
        (run_id,),
    ).fetchall()
    return map_rows(rows, AGENT_STEP_COLUMNS)


def list_handoffs(connection: Connection, run_id: UUID) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT handoff_id, run_id, from_agent, to_agent, status, started_at,
                  finished_at, duration_ms, error_source, error_code
             FROM handoff_logs WHERE run_id = %s
            ORDER BY started_at, handoff_id""",
        (run_id,),
    ).fetchall()
    return map_rows(rows, HANDOFF_COLUMNS)


def list_api_call_logs(
    connection: Connection,
    limit: int,
    offset: int,
    provider: str | None,
    api_type: str | None,
    success: bool | None,
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    where_clauses: list[str] = []
    parameters: list[Any] = []
    if provider is not None:
        where_clauses.append('provider = %s')
        parameters.append(provider)
    if api_type is not None:
        where_clauses.append('api_type = %s')
        parameters.append(api_type)
    if success is not None:
        where_clauses.append('success = %s')
        parameters.append(success)
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ''
    summary = connection.execute(
        f"""SELECT count(*)::integer,
                  count(*) FILTER (WHERE success)::integer,
                  count(*) FILTER (WHERE NOT success)::integer,
                  CASE WHEN count(*) = 0 THEN 0
                       ELSE round(100.0 * count(*) FILTER (WHERE success) / count(*), 2)
                  END,
                  round(avg(latency_ms))::integer
             FROM api_call_logs {where_sql}""",
        parameters,
    ).fetchone()
    total = int(summary[0])
    rows = connection.execute(
        f"""SELECT api_call_id, run_id, provider, api_type, http_status, latency_ms,
                    success, called_at, error_source, error_code
               FROM api_call_logs {where_sql}
              ORDER BY called_at DESC, api_call_id DESC
              LIMIT %s OFFSET %s""",
        [*parameters, limit, offset],
    ).fetchall()
    stats = {
        'total': int(summary[0]),
        'success': int(summary[1]),
        'failed': int(summary[2]),
        'success_rate': float(summary[3]),
        'avg_latency_ms': summary[4],
    }
    return total, map_rows(rows, API_CALL_COLUMNS), stats


def list_incidents(
    connection: Connection,
    limit: int,
    offset: int,
    status: str | None,
    error_source: str | None,
) -> tuple[int, list[dict[str, Any]]]:
    where_clauses: list[str] = []
    parameters: list[Any] = []
    if status is not None:
        where_clauses.append('status = %s')
        parameters.append(status)
    if error_source is not None:
        where_clauses.append('error_source = %s')
        parameters.append(error_source)
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ''
    total = connection.execute(
        f'SELECT count(*) FROM incident_logs {where_sql}', parameters
    ).fetchone()[0]
    rows = connection.execute(
        f"""SELECT incident_id, run_id, api_call_id, status, severity, error_source,
                    error_code, error_message, failed_step_name, occurred_at, resolved_at
               FROM incident_logs {where_sql}
              ORDER BY occurred_at DESC, incident_id DESC
              LIMIT %s OFFSET %s""",
        [*parameters, limit, offset],
    ).fetchall()
    return int(total), map_rows(rows, INCIDENT_COLUMNS)


def list_recoveries_for_incidents(
    connection: Connection,
    incident_ids: list[UUID],
) -> list[dict[str, Any]]:
    if not incident_ids:
        return []
    rows = connection.execute(
        """SELECT recovery_id, incident_id, run_id, attempt_number, recovery_action,
                  status, started_at, finished_at, duration_ms
             FROM recovery_logs
            WHERE incident_id = ANY(%s)
            ORDER BY incident_id, attempt_number""",
        (incident_ids,),
    ).fetchall()
    return map_rows(rows, RECOVERY_COLUMNS)


def check_database(connection: Connection) -> bool:
    connection.execute('SELECT 1')
    return True
