"""Retry policy shared by the durable itinerary worker."""
from __future__ import annotations

from dataclasses import dataclass

from psycopg import InterfaceError, OperationalError

from app.core.database import DatabaseUnavailable


MAX_WORKER_ATTEMPTS = 3
BASE_RETRY_DELAY_SECONDS = 2
MAX_RETRY_DELAY_SECONDS = 30


@dataclass(frozen=True)
class RecoveryDecision:
    retryable: bool
    error_source: str
    error_code: str
    safe_message: str
    recovery_action: str


def classify_worker_error(error: Exception, stage: str) -> RecoveryDecision:
    """Classify retryable infrastructure failures without logging raw errors."""
    error_code = getattr(error, 'error_code', None)
    if isinstance(error_code, str) and error_code.startswith('culture_api_'):
        is_retryable = bool(getattr(error, 'retryable', False))
        return RecoveryDecision(
            retryable=is_retryable,
            error_source='EXTERNAL_API',
            error_code=error_code.upper(),
            safe_message='Seoul cultural events API request failed.',
            recovery_action='retry_seoul_culture_api_request',
        )

    if isinstance(error, (DatabaseUnavailable, OperationalError, InterfaceError)):
        return RecoveryDecision(
            retryable=True,
            error_source='DATABASE',
            error_code='DATABASE_OPERATION_FAILED',
            safe_message='A transient PostgreSQL operation failed.',
            recovery_action='retry_after_database_reconnect',
        )

    if error_code == 'DATABASE_OPERATION_FAILED':
        return RecoveryDecision(
            retryable=True,
            error_source='DATABASE',
            error_code='DATABASE_OPERATION_FAILED',
            safe_message='A transient PostgreSQL operation failed.',
            recovery_action='retry_after_database_reconnect',
        )

    if stage == 'langgraph_execution' and isinstance(error_code, str) and error_code.startswith('LLM'):
        return RecoveryDecision(
            retryable=False,
            error_source='LLM',
            error_code=error_code.upper(),
            safe_message='The bounded LLM retries were exhausted or the error was not retryable.',
            recovery_action='none',
        )

    if stage == 'langgraph_execution' and error_code == 'LANGGRAPH_EXECUTION_FAILED':
        return RecoveryDecision(
            retryable=True,
            error_source='LANGGRAPH',
            error_code='LANGGRAPH_EXECUTION_FAILED',
            safe_message='The LangGraph runtime failed; a bounded worker retry will be attempted.',
            recovery_action='retry_langgraph_worker',
        )

    if stage == 'langgraph_execution' and bool(getattr(error, 'retryable', False)):
        return RecoveryDecision(
            retryable=True,
            error_source='LLM',
            error_code='LLM_AGENT_CALL_FAILED',
            safe_message='A transient LLM request failed after bounded in-node retries.',
            recovery_action='retry_recommendation_worker',
        )

    return RecoveryDecision(
        retryable=False,
        error_source='LANGGRAPH' if stage == 'langgraph_execution' else 'UNKNOWN',
        error_code=(str(error_code).upper() if isinstance(error_code, str)
                    else 'ITINERARY_GENERATION_FAILED'),
        safe_message='The recommendation worker could not complete this run.',
        recovery_action='none',
    )


def retry_delay_seconds(attempt_number: int) -> int:
    """Return bounded exponential backoff after the failed attempt."""
    exponent = max(0, attempt_number - 1)
    return min(BASE_RETRY_DELAY_SECONDS * (2 ** exponent), MAX_RETRY_DELAY_SECONDS)
