import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection

from app.repositories.operation_logs_repository import (
    create_agent_run,
    create_run_component_logs,
)


class VisitorSessionNotActive(RuntimeError):
    pass


class ItineraryGroupNotFound(RuntimeError):
    pass


class RegenerationLimitReached(RuntimeError):
    pass


class GenerationAlreadyRunning(RuntimeError):
    pass


def get_active_visitor_session_expiration(
    connection: Connection,
    visitor_session_id: UUID,
) -> datetime | None:
    row = connection.execute(
        """SELECT expires_at
             FROM visitor_sessions
            WHERE id = %s AND revoked_at IS NULL AND expires_at > now()""",
        (visitor_session_id,),
    ).fetchone()
    return row[0] if row is not None else None


def visitor_session_is_active(connection: Connection, visitor_session_id: UUID) -> bool:
    return get_active_visitor_session_expiration(connection, visitor_session_id) is not None


def create_initial_execution(
    connection: Connection,
    visitor_session_id: UUID,
    session_expires_at: datetime,
    request_data: dict[str, Any],
) -> tuple[UUID, UUID]:
    group_id = uuid4()
    run_id = uuid4()

    with connection.transaction():
        connection.execute(
            """INSERT INTO visitor_sessions (id, expires_at)
               VALUES (%s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (visitor_session_id, session_expires_at),
        )
        if not visitor_session_is_active(connection, visitor_session_id):
            raise VisitorSessionNotActive

        connection.execute(
            """INSERT INTO itinerary_groups (
                   group_id, visitor_session_id, origin_stop_id, destination_stop_id,
                   service_date, preferred_departure_time, companion_type, activity_type,
                   expires_at
               ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                group_id,
                visitor_session_id,
                request_data['origin_stop_id'],
                request_data['destination_stop_id'],
                request_data['service_date'],
                request_data['preferred_departure_time'],
                request_data['companion_type'],
                request_data['activity_type'],
                session_expires_at,
            ),
        )
        create_agent_run(connection, run_id)
        create_run_component_logs(connection, run_id)
        connection.execute(
            """INSERT INTO itinerary_executions (
                   run_id, group_id, generation_index, status, queue_status
               ) VALUES (%s, %s, 0, 'RUNNING', 'QUEUED')""",
            (run_id, group_id),
        )

    return group_id, run_id


def create_regeneration_execution(
    connection: Connection,
    visitor_session_id: UUID,
    group_id: UUID,
) -> tuple[UUID, int]:
    with connection.transaction():
        group = connection.execute(
            """SELECT group_id, regeneration_count
                 FROM itinerary_groups
                WHERE group_id = %s
                  AND visitor_session_id = %s
                  AND expires_at > now()
                  AND EXISTS (
                      SELECT 1 FROM visitor_sessions
                       WHERE id = itinerary_groups.visitor_session_id
                         AND revoked_at IS NULL AND expires_at > now()
                  )
                FOR UPDATE""",
            (group_id, visitor_session_id),
        ).fetchone()
        if group is None:
            raise ItineraryGroupNotFound

        active_execution = connection.execute(
            """SELECT run_id, generation_index
                 FROM itinerary_executions
                WHERE group_id = %s AND status = 'RUNNING'
                ORDER BY accepted_at DESC
                LIMIT 1""",
            (group_id,),
        ).fetchone()
        if active_execution is not None:
            if int(active_execution[1]) == 0:
                raise GenerationAlreadyRunning
            return active_execution[0], int(active_execution[1])

        regeneration_count = int(group[1])
        if regeneration_count >= 2:
            raise RegenerationLimitReached

        generation_index = regeneration_count + 1
        run_id = uuid4()
        create_agent_run(connection, run_id)
        create_run_component_logs(connection, run_id)
        connection.execute(
            """INSERT INTO itinerary_executions (
                   run_id, group_id, generation_index, status, queue_status
               ) VALUES (%s, %s, %s, 'RUNNING', 'QUEUED')""",
            (run_id, group_id, generation_index),
        )

    return run_id, generation_index


def claim_next_execution(
    connection: Connection,
    worker_id: str,
    lease_seconds: int = 90,
) -> dict[str, Any] | None:
    with connection.transaction():
        execution = connection.execute(
            """SELECT run_id, group_id, generation_index, worker_attempt_count,
                      queue_status, worker_lease_until
                 FROM itinerary_executions
                WHERE status = 'RUNNING'
                  AND (
                      (queue_status = 'QUEUED'
                       AND (next_retry_at IS NULL OR next_retry_at <= now()))
                      OR (queue_status = 'CLAIMED' AND worker_lease_until < now())
                  )
                ORDER BY accepted_at, run_id
                FOR UPDATE SKIP LOCKED
                LIMIT 1"""
        ).fetchone()
        if execution is None:
            return None

        run_id, group_id, generation_index, attempt_count, queue_status, _lease_until = execution
        lease_expired = queue_status == 'CLAIMED'
        connection.execute(
            """UPDATE itinerary_executions
                  SET queue_status = 'CLAIMED',
                      worker_id = %s,
                      worker_lease_until = now() + (%s * interval '1 second'),
                      worker_attempt_count = worker_attempt_count + 1,
                      next_retry_at = NULL,
                      execution_started_at = COALESCE(execution_started_at, now())
                WHERE run_id = %s""",
            (worker_id, lease_seconds, run_id),
        )
        group = connection.execute(
            """SELECT origin_stop_id, destination_stop_id, service_date,
                      preferred_departure_time, companion_type, activity_type
                 FROM itinerary_groups
                WHERE group_id = %s""",
            (group_id,),
        ).fetchone()
        if group is None:
            raise ItineraryGroupNotFound

        return {
            'run_id': run_id,
            'group_id': group_id,
            'generation_index': int(generation_index),
            'worker_attempt_count': int(attempt_count) + 1,
            'lease_expired': lease_expired,
            'origin_stop_id': group[0],
            'destination_stop_id': group[1],
            'service_date': group[2],
            'preferred_departure_time': group[3],
            'companion_type': group[4],
            'activity_type': group[5],
        }


def get_execution_for_session(
    connection: Connection,
    run_id: UUID,
    visitor_session_id: UUID,
) -> dict[str, Any] | None:
    row = connection.execute(
        """SELECT execution.group_id, execution.run_id, execution.generation_index,
                  execution.status, execution.accepted_at, execution.execution_started_at,
                  execution.completed_at, execution.reason_code, execution.reason_message,
                  execution.error_code, execution.error_message, execution.retryable,
                  version.itinerary_version_id, version.itinerary_json
             FROM itinerary_executions AS execution
             JOIN itinerary_groups AS group_row
               ON group_row.group_id = execution.group_id
             JOIN visitor_sessions AS visitor
               ON visitor.id = group_row.visitor_session_id
             LEFT JOIN itinerary_versions AS version
               ON version.execution_id = execution.run_id
            WHERE execution.run_id = %s
              AND group_row.visitor_session_id = %s
              AND group_row.expires_at > now()
              AND visitor.revoked_at IS NULL
              AND visitor.expires_at > now()""",
        (run_id, visitor_session_id),
    ).fetchone()
    if row is None:
        return None

    itinerary_payload = row[13]
    if itinerary_payload is not None:
        itinerary_payload = dict(itinerary_payload)
        itinerary_payload['id'] = str(row[12])

    return {
        'group_id': row[0],
        'run_id': row[1],
        'generation_index': int(row[2]),
        'status': row[3],
        'accepted_at': row[4],
        'execution_started_at': row[5],
        'completed_at': row[6],
        'reason': (
            {'code': row[7], 'message': row[8]} if row[7] is not None else None
        ),
        'error': (
            {'code': row[9], 'message': row[10], 'retryable': row[11]}
            if row[9] is not None
            else None
        ),
        'itinerary': itinerary_payload,
    }


def complete_execution_with_itinerary(
    connection: Connection,
    execution: dict[str, Any],
    itinerary: dict[str, Any],
    source_snapshot: dict[str, Any],
    validation_status: str,
) -> UUID:
    with connection.transaction():
        version_id = uuid4()
        serialized_itinerary = json.dumps(itinerary, ensure_ascii=False)
        connection.execute(
            """INSERT INTO itinerary_versions (
                   itinerary_version_id, group_id, version, execution_id, title,
                   itinerary_json, source_snapshot, validation_status
               ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)""",
            (
                version_id,
                execution['group_id'],
                execution['generation_index'],
                execution['run_id'],
                itinerary['title'],
                serialized_itinerary,
                json.dumps(source_snapshot, ensure_ascii=False),
                validation_status,
            ),
        )

        for item in itinerary['items']:
            location = item.get('location') or {}
            departure_stop = item.get('departure_stop') or {}
            arrival_stop = item.get('arrival_stop') or {}
            connection.execute(
                """INSERT INTO itinerary_stops (
                       itinerary_version_id, sequence_no, item_type, title,
                       start_at, end_at, reason, sources, latitude, longitude,
                       place_name, address, departure_stop_id, departure_stop_name,
                       arrival_stop_id, arrival_stop_name
                   ) VALUES (
                       %s, %s, %s, %s, %s::timestamptz, %s::timestamptz, %s,
                       %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s
                   )""",
                (
                    version_id,
                    item['sequence_no'],
                    item['item_type'],
                    item['title'],
                    item.get('start_at'),
                    item.get('end_at'),
                    item['reason'],
                    json.dumps(item.get('sources', []), ensure_ascii=False),
                    location.get('latitude'),
                    location.get('longitude'),
                    item.get('place_name'),
                    item.get('address'),
                    departure_stop.get('stop_id'),
                    departure_stop.get('name'),
                    arrival_stop.get('stop_id'),
                    arrival_stop.get('name'),
                ),
            )

        connection.execute(
            """UPDATE itinerary_executions
                  SET status = 'SUCCESS', queue_status = 'DONE', completed_at = now(),
                      worker_lease_until = NULL,
                      worker_id = NULL, next_retry_at = NULL,
                      reason_code = NULL, reason_message = NULL,
                      error_code = NULL, error_message = NULL, retryable = NULL
                WHERE run_id = %s AND status = 'RUNNING'""",
            (execution['run_id'],),
        )
        if execution['generation_index'] > 0:
            connection.execute(
                """UPDATE itinerary_groups
                      SET regeneration_count = GREATEST(regeneration_count, %s)
                    WHERE group_id = %s""",
                (execution['generation_index'], execution['group_id']),
            )
    return version_id


def complete_execution_without_result(
    connection: Connection,
    run_id: UUID,
    reason_code: str,
    reason_message: str,
) -> None:
    connection.execute(
        """UPDATE itinerary_executions
              SET status = 'NO_RESULT', queue_status = 'DONE', completed_at = now(),
                  worker_lease_until = NULL, worker_id = NULL, next_retry_at = NULL,
                  reason_code = %s, reason_message = %s,
                  error_code = NULL, error_message = NULL, retryable = NULL
            WHERE run_id = %s AND status = 'RUNNING'""",
        (reason_code, reason_message, run_id),
    )


def fail_execution(
    connection: Connection,
    run_id: UUID,
    error_code: str,
    error_message: str,
    retryable: bool,
) -> None:
    connection.execute(
        """UPDATE itinerary_executions
              SET status = 'FAILED', queue_status = 'DONE', completed_at = now(),
                  worker_lease_until = NULL, worker_id = NULL, next_retry_at = NULL,
                  reason_code = NULL, reason_message = NULL,
                  error_code = %s, error_message = %s, retryable = %s
            WHERE run_id = %s AND status = 'RUNNING'""",
        (error_code, error_message, retryable, run_id),
    )


def renew_execution_lease(
    connection: Connection,
    run_id: UUID,
    worker_id: str,
    lease_seconds: int = 90,
) -> bool:
    row = connection.execute(
        """UPDATE itinerary_executions
              SET worker_lease_until = now() + (%s * interval '1 second')
            WHERE run_id = %s AND status = 'RUNNING'
              AND queue_status = 'CLAIMED' AND worker_id = %s
         RETURNING run_id""",
        (lease_seconds, run_id, worker_id),
    ).fetchone()
    return row is not None
