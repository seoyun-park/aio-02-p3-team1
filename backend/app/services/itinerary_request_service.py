from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection

from app.repositories import itinerary_repository as repository
from app.repositories.operation_logs_repository import finish_agent_run
from app.recovery.controller import finish_run_recoveries
from app.schemas.itinerary import ItineraryCreateRequest, ItineraryResponse
from app.services.timetable_service import get_departure_estimate


VISITOR_SESSION_TTL = timedelta(hours=8)


def resolve_or_create_visitor_session(
    connection: Connection,
    session_value: str | None,
) -> tuple[UUID, datetime]:
    now = datetime.now(timezone.utc)
    visitor_session_id = parse_visitor_session_id(session_value)
    if visitor_session_id is not None:
        active_expiration = repository.get_active_visitor_session_expiration(
            connection,
            visitor_session_id,
        )
        if active_expiration is not None:
            return visitor_session_id, active_expiration

    return uuid4(), now + VISITOR_SESSION_TTL


def parse_visitor_session_id(session_value: str | None) -> UUID | None:
    if not session_value:
        return None
    try:
        return UUID(session_value)
    except (TypeError, ValueError):
        return None


def create_initial_itinerary_execution(
    connection: Connection,
    visitor_session_id: UUID,
    session_expires_at: datetime,
    payload: ItineraryCreateRequest,
) -> tuple[UUID, UUID]:
    request_data = payload.model_dump()
    return repository.create_initial_execution(
        connection,
        visitor_session_id,
        session_expires_at,
        request_data,
    )


def validate_itinerary_route(payload: ItineraryCreateRequest) -> None:
    get_departure_estimate(
        payload.origin_stop_id,
        payload.destination_stop_id,
        payload.service_date,
        payload.preferred_departure_time,
    )


def create_regeneration_execution(
    connection: Connection,
    visitor_session_id: UUID,
    group_id: UUID,
) -> tuple[UUID, int]:
    return repository.create_regeneration_execution(
        connection,
        visitor_session_id,
        group_id,
    )


def get_itinerary_run_status(
    connection: Connection,
    run_id: UUID,
    visitor_session_id: UUID,
) -> dict[str, object] | None:
    return repository.get_execution_for_session(
        connection,
        run_id,
        visitor_session_id,
    )


def finish_execution_as_no_result(
    connection: Connection,
    run_id: UUID,
    reason_code: str,
    reason_message: str,
) -> None:
    with connection.transaction():
        repository.complete_execution_without_result(
            connection,
            run_id,
            reason_code,
            reason_message,
        )
        finish_run_recoveries(connection, run_id, 'RECOVERED')
        finish_agent_run(connection, run_id, 'SUCCESS', None, None, None)


def finish_execution_as_failed(
    connection: Connection,
    run_id: UUID,
    error_code: str,
    error_message: str,
    retryable: bool,
) -> None:
    with connection.transaction():
        repository.fail_execution(
            connection,
            run_id,
            error_code,
            error_message,
            retryable,
        )
        finish_run_recoveries(connection, run_id, 'FAILED')
        finish_agent_run(
            connection,
            run_id,
            'FAILED',
            error_source='UNKNOWN',
            error_code=error_code,
            failed_step_name='recommendation_worker',
        )


def finish_execution_with_itinerary(
    connection: Connection,
    execution: dict[str, Any],
    itinerary: ItineraryResponse,
    source_snapshot: dict[str, Any],
) -> UUID:
    itinerary_payload = itinerary.model_dump(mode='json', exclude={'id'})
    with connection.transaction():
        version_id = repository.complete_execution_with_itinerary(
            connection,
            execution,
            itinerary_payload,
            source_snapshot,
            validation_status='POSSIBLE',
        )
        finish_run_recoveries(connection, execution['run_id'], 'RECOVERED')
        finish_agent_run(connection, execution['run_id'], 'SUCCESS', None, None, None)
    return version_id
