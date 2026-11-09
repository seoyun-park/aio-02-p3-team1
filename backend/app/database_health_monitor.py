"""Watch PostgreSQL and spool outage transitions until the DB is writable."""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import psycopg


LOGGER = logging.getLogger('database_health_monitor')
POLL_INTERVAL_SECONDS = max(2, int(os.getenv('DATABASE_MONITOR_INTERVAL_SECONDS', '10')))
FAILURE_THRESHOLD = max(1, int(os.getenv('DATABASE_MONITOR_FAILURE_THRESHOLD', '2')))
SPOOL_PATH = Path(os.getenv(
    'DATABASE_MONITOR_SPOOL_PATH',
    '/var/lib/seoul-wave-health-monitor/monitor.sqlite3',
))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def connect_database() -> psycopg.Connection:
    password = os.getenv('DATABASE_PASSWORD', '')
    if not password:
        raise RuntimeError('DATABASE_PASSWORD is not configured.')
    return psycopg.connect(
        host=os.getenv('DATABASE_HOST', 'db'),
        port=os.getenv('DATABASE_PORT', '5432'),
        dbname=os.getenv('DATABASE_NAME', 'seoul_wave'),
        user=os.getenv('DATABASE_USER', 'seoul_wave_app'),
        password=password,
        connect_timeout=3,
        autocommit=True,
    )


def initialize_spool() -> None:
    SPOOL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(SPOOL_PATH) as spool:
        spool.execute('PRAGMA journal_mode=WAL')
        spool.execute(
            """CREATE TABLE IF NOT EXISTS monitor_state (
                   singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                   status TEXT NOT NULL,
                   failure_count INTEGER NOT NULL DEFAULT 0,
                   first_failure_at TEXT
               )"""
        )
        spool.execute(
            """CREATE TABLE IF NOT EXISTS database_outages (
                   monitor_event_id TEXT PRIMARY KEY,
                   detected_at TEXT NOT NULL,
                   recovered_at TEXT,
                   flushed INTEGER NOT NULL DEFAULT 0 CHECK (flushed IN (0, 1))
               )"""
        )


def probe_database() -> bool:
    try:
        with connect_database() as connection:
            connection.execute('SELECT 1').fetchone()
        return True
    except (psycopg.Error, RuntimeError, OSError):
        return False


def record_probe_result(is_up: bool, observed_at: datetime) -> None:
    observed_text = observed_at.isoformat()
    with sqlite3.connect(SPOOL_PATH) as spool:
        state = spool.execute(
            'SELECT status, failure_count, first_failure_at FROM monitor_state WHERE singleton_id = 1'
        ).fetchone()
        if state is None:
            if is_up:
                spool.execute(
                    "INSERT INTO monitor_state (singleton_id, status) VALUES (1, 'UP')"
                )
            else:
                spool.execute(
                    """INSERT INTO monitor_state
                           (singleton_id, status, failure_count, first_failure_at)
                         VALUES (1, 'SUSPECT', 1, ?)""",
                    (observed_text,),
                )
            return

        status, failure_count, first_failure_at = state
        if is_up:
            if status == 'DOWN':
                outage = spool.execute(
                    """SELECT monitor_event_id FROM database_outages
                         WHERE recovered_at IS NULL ORDER BY detected_at DESC LIMIT 1"""
                ).fetchone()
                if outage is not None:
                    spool.execute(
                        "UPDATE database_outages SET recovered_at = ? WHERE monitor_event_id = ?",
                        (observed_text, outage[0]),
                    )
            spool.execute(
                """UPDATE monitor_state
                      SET status = 'UP', failure_count = 0, first_failure_at = NULL
                    WHERE singleton_id = 1"""
            )
            return

        if status == 'UP':
            spool.execute(
                """UPDATE monitor_state
                      SET status = 'SUSPECT', failure_count = 1, first_failure_at = ?
                    WHERE singleton_id = 1""",
                (observed_text,),
            )
            return

        failure_count = int(failure_count) + 1
        if status != 'DOWN' and failure_count >= FAILURE_THRESHOLD:
            event_id = str(UUID(bytes=os.urandom(16), version=4))
            spool.execute(
                """INSERT INTO database_outages (monitor_event_id, detected_at)
                     VALUES (?, ?)""",
                (event_id, first_failure_at or observed_text),
            )
            status = 'DOWN'
        spool.execute(
            """UPDATE monitor_state
                  SET status = ?, failure_count = ?
                WHERE singleton_id = 1""",
            (status, failure_count),
        )


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def flush_recovered_outages() -> None:
    with sqlite3.connect(SPOOL_PATH) as spool:
        outages = spool.execute(
            """SELECT monitor_event_id, detected_at, recovered_at
                 FROM database_outages
                WHERE flushed = 0 AND recovered_at IS NOT NULL
                ORDER BY detected_at"""
        ).fetchall()
    if not outages:
        return

    with connect_database() as connection:
        schema_ready = connection.execute(
            """SELECT to_regclass('public.incident_logs'),
                      to_regclass('public.recovery_logs')"""
        ).fetchone()
        if not schema_ready or schema_ready[0] is None or schema_ready[1] is None:
            return

        for event_id_text, detected_text, recovered_text in outages:
            event_id = UUID(event_id_text)
            detected_at = parse_timestamp(detected_text)
            recovered_at = parse_timestamp(recovered_text)
            duration_ms = max(0, round((recovered_at - detected_at).total_seconds() * 1000))
            with connection.transaction():
                incident = connection.execute(
                    """INSERT INTO incident_logs (
                               monitor_event_id, run_id, status, severity, error_source,
                               error_code, error_message, failed_step_name,
                               occurred_at, resolved_at
                           ) VALUES (
                               %s, NULL, 'RESOLVED', 'CRITICAL', 'DATABASE',
                               'DATABASE_CONNECTIVITY_INTERRUPTED',
                               'PostgreSQL became unreachable and later recovered.',
                               'database_health_monitor', %s, %s
                           )
                           ON CONFLICT (monitor_event_id)
                               WHERE monitor_event_id IS NOT NULL
                           DO UPDATE SET monitor_event_id = EXCLUDED.monitor_event_id
                           RETURNING incident_id""",
                    (event_id, detected_at, recovered_at),
                ).fetchone()
                incident_id = incident[0]
                connection.execute(
                    """INSERT INTO recovery_logs (
                               incident_id, run_id, attempt_number, recovery_action,
                               status, started_at, finished_at, duration_ms
                           ) VALUES (
                               %s, NULL, 1, 'postgresql_connection_restored',
                               'RECOVERED', %s, %s, %s
                           ) ON CONFLICT (incident_id, attempt_number) DO NOTHING""",
                    (incident_id, detected_at, recovered_at, duration_ms),
                )
                connection.execute(
                    """UPDATE incident_logs
                          SET status = 'RESOLVED',
                              resolved_at = COALESCE(resolved_at, %s)
                        WHERE incident_id = %s""",
                    (recovered_at, incident_id),
                )
            with sqlite3.connect(SPOOL_PATH) as spool:
                spool.execute(
                    'UPDATE database_outages SET flushed = 1 WHERE monitor_event_id = ?',
                    (event_id_text,),
                )
            LOGGER.warning('Recorded a recovered PostgreSQL outage as incident %s.', incident_id)


def run_monitor_iteration() -> bool:
    """Probe PostgreSQL once and keep transient spool failures from killing the monitor."""
    observed_at = utc_now()
    is_up = probe_database()
    record_probe_result(is_up, observed_at)
    if is_up:
        try:
            flush_recovered_outages()
        except (psycopg.Error, RuntimeError, OSError, sqlite3.Error):
            LOGGER.warning('Recovered database outage events remain in the local spool.')
    else:
        LOGGER.error('PostgreSQL probe failed; outage state is held in the local spool.')
    return is_up


def run_monitor() -> None:
    logging.basicConfig(level=logging.INFO)
    initialize_spool()
    LOGGER.info('PostgreSQL health monitor started; polling every %s seconds.', POLL_INTERVAL_SECONDS)
    while True:
        run_monitor_iteration()
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == '__main__':
    run_monitor()
