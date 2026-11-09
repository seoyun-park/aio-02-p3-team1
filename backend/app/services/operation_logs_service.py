from typing import Any
from uuid import UUID

from psycopg import Connection

from app.core.error_sanitizer import sanitize_error_message
from app.models.operation_logs import (
    API_TYPES,
    ERROR_SOURCES,
    INCIDENT_SEVERITIES,
)
from app.repositories import operation_logs_repository as repository


def start_agent_run(connection: Connection, run_id: UUID | None = None) -> UUID:
    return repository.create_agent_run(connection, run_id)


def finish_agent_run(
    connection: Connection,
    run_id: UUID,
    status: str,
    error_source: str | None = None,
    error_code: str | None = None,
    failed_step_name: str | None = None,
) -> dict[str, Any] | None:
    validate_choice(status, {'SUCCESS', 'FAILED'}, 'status')
    return repository.finish_agent_run(
        connection,
        run_id,
        status,
        validate_optional_choice(error_source, ERROR_SOURCES, 'error_source'),
        sanitize_error_message(error_code),
        sanitize_label(failed_step_name, 100),
    )


def start_agent_step(
    connection: Connection,
    run_id: UUID,
    step_order: int,
    agent_name: str,
    step_name: str,
) -> UUID:
    if step_order < 1:
        raise ValueError('step_order must be positive.')
    return repository.create_agent_step_log(
        connection,
        run_id,
        step_order,
        sanitize_required_label(agent_name, 100, 'agent_name'),
        sanitize_required_label(step_name, 100, 'step_name'),
    )


def finish_agent_step(
    connection: Connection,
    step_log_id: UUID,
    status: str,
    error_source: str | None = None,
    error_code: str | None = None,
) -> dict[str, Any] | None:
    validate_choice(status, {'SUCCESS', 'FAILED'}, 'status')
    return repository.finish_agent_step_log(
        connection,
        step_log_id,
        status,
        validate_optional_choice(error_source, ERROR_SOURCES, 'error_source'),
        sanitize_error_message(error_code),
    )


def record_api_call(
    connection: Connection,
    run_id: UUID,
    provider: str,
    api_type: str,
    http_status: int | None,
    latency_ms: int,
    success: bool,
    error_source: str | None = None,
    error_code: str | None = None,
) -> UUID:
    validate_choice(api_type, API_TYPES, 'api_type')
    validate_optional_choice(error_source, ERROR_SOURCES, 'error_source')
    if latency_ms < 0:
        raise ValueError('latency_ms cannot be negative.')
    if http_status is not None and not 100 <= http_status <= 599:
        raise ValueError('http_status must be between 100 and 599.')
    return repository.create_api_call_log(
        connection,
        run_id,
        sanitize_required_label(provider, 100, 'provider'),
        api_type,
        http_status,
        latency_ms,
        success,
        error_source,
        sanitize_error_message(error_code),
    )


def start_handoff(
    connection: Connection,
    run_id: UUID,
    from_agent: str,
    to_agent: str,
) -> UUID:
    return repository.create_handoff_log(
        connection,
        run_id,
        sanitize_required_label(from_agent, 100, 'from_agent'),
        sanitize_required_label(to_agent, 100, 'to_agent'),
    )


def finish_handoff(
    connection: Connection,
    handoff_id: UUID,
    status: str,
    error_source: str | None = None,
    error_code: str | None = None,
) -> dict[str, Any] | None:
    validate_choice(status, {'SUCCESS', 'FAILED'}, 'status')
    return repository.finish_handoff_log(
        connection,
        handoff_id,
        status,
        validate_optional_choice(error_source, ERROR_SOURCES, 'error_source'),
        sanitize_error_message(error_code),
    )


def create_incident(
    connection: Connection,
    run_id: UUID,
    severity: str,
    error_source: str,
    error_code: str,
    error_message: str | None = None,
    failed_step_name: str | None = None,
    api_call_id: UUID | None = None,
) -> UUID:
    validate_choice(severity, INCIDENT_SEVERITIES, 'severity')
    validate_choice(error_source, ERROR_SOURCES, 'error_source')
    return repository.create_incident_log(
        connection,
        run_id,
        api_call_id,
        severity,
        error_source,
        sanitize_required_label(error_code, 100, 'error_code'),
        sanitize_error_message(error_message),
        sanitize_label(failed_step_name, 100),
    )


def start_recovery(
    connection: Connection,
    incident_id: UUID,
    run_id: UUID,
    attempt_number: int,
    recovery_action: str,
) -> UUID:
    if attempt_number < 1:
        raise ValueError('attempt_number must be positive.')
    with connection.transaction():
        recovery_id = repository.start_recovery_log(
            connection,
            incident_id,
            run_id,
            attempt_number,
            sanitize_required_label(recovery_action, 100, 'recovery_action'),
        )
        if recovery_id is None:
            raise ValueError('incident is missing or already resolved.')
        return recovery_id


def finish_recovery(
    connection: Connection,
    recovery_id: UUID,
    status: str,
) -> dict[str, Any] | None:
    validate_choice(status, {'RECOVERED', 'FAILED'}, 'status')
    with connection.transaction():
        return repository.finish_recovery_log(connection, recovery_id, status)


def validate_choice(value: str, allowed_values: set[str] | frozenset[str], field_name: str) -> None:
    if value not in allowed_values:
        raise ValueError(f'{field_name} has an unsupported value.')


def validate_optional_choice(
    value: str | None,
    allowed_values: set[str] | frozenset[str],
    field_name: str,
) -> str | None:
    if value is not None:
        validate_choice(value, allowed_values, field_name)
    return value


def sanitize_label(value: str | None, max_length: int) -> str | None:
    sanitized_value = sanitize_error_message(value)
    return sanitized_value[:max_length] if sanitized_value else None


def sanitize_required_label(value: str, max_length: int, field_name: str) -> str:
    sanitized_value = sanitize_label(value, max_length)
    if not sanitized_value:
        raise ValueError(f'{field_name} is required.')
    return sanitized_value
