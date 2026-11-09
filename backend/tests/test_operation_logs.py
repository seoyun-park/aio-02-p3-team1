import os

import psycopg
import pytest

from app.core.error_sanitizer import sanitize_error_message
from app.main import app
from app.services.operation_logs_service import finish_agent_run, start_agent_run


@pytest.fixture
def operation_connection():
    connection = psycopg.connect(
        host=os.environ['DATABASE_HOST'],
        port=os.environ['DATABASE_PORT'],
        dbname=os.environ['DATABASE_NAME'],
        user=os.environ['DATABASE_USER'],
        password=os.environ['DATABASE_PASSWORD'],
    )
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def test_error_sanitizer_redacts_credentials_urls_and_personal_data():
    sanitized_message = sanitize_error_message(
        'failure https://example.test/secret?api_key=secret-value '
        'password=bad-value Bearer token-value person@example.com 010-1234-5678'
    )

    assert sanitized_message is not None
    for sensitive_value in (
        'example.test',
        'secret-value',
        'bad-value',
        'token-value',
        'person@example.com',
        '010-1234-5678',
    ):
        assert sensitive_value not in sanitized_message

    header_message = sanitize_error_message(
        'Authorization: Bearer header-secret API key=key-secret'
    )
    assert header_message is not None
    assert 'header-secret' not in header_message
    assert 'key-secret' not in header_message


def test_user_service_does_not_register_operator_dashboard_routes():
    registered_paths = set(app.openapi()['paths'])

    assert not any(path.startswith('/api/v1/admin/') for path in registered_paths)
    assert not any(path.startswith('/api/admin/') for path in registered_paths)


def test_log_writer_rejects_status_outside_database_contract(operation_connection):
    run_id = start_agent_run(operation_connection)

    with pytest.raises(ValueError, match='unsupported value'):
        finish_agent_run(operation_connection, run_id, 'SUCCEEDED')
