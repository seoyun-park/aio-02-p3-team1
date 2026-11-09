import os
from collections.abc import Generator

import psycopg
from psycopg import Connection


class DatabaseUnavailable(RuntimeError):
    """Raised when the application cannot use PostgreSQL."""


def get_database_connection() -> Generator[Connection, None, None]:
    connection = create_database_connection()
    try:
        yield connection
    except psycopg.Error as error:
        raise DatabaseUnavailable('PostgreSQL is unavailable.') from error
    finally:
        connection.close()


def create_database_connection(autocommit: bool = True) -> Connection:
    database_password = os.environ.get('DATABASE_PASSWORD')
    if not database_password:
        raise DatabaseUnavailable('Database connection is not configured.')

    try:
        connection = psycopg.connect(
            host=os.environ.get('DATABASE_HOST', 'db'),
            port=os.environ.get('DATABASE_PORT', '5432'),
            dbname=os.environ.get('DATABASE_NAME', 'seoul_wave'),
            user=os.environ.get('DATABASE_USER', 'seoul_wave_app'),
            password=database_password,
            connect_timeout=3,
            autocommit=autocommit,
        )
    except psycopg.Error as error:
        raise DatabaseUnavailable('PostgreSQL is unavailable.') from error
    return connection
