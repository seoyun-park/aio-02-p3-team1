"""Durability and ownership checks for the recovery paths used in deployment."""
import os
import sqlite3
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import database_health_monitor as monitor, itinerary_worker as worker
from app.agents.demo_models import create_demo_agent_models
from app.main import app
from app.recovery import controller, policies
from app.repositories import itinerary_repository as repository
from app.models.timetable import Stop, TimetableSnapshot, TripEstimate
from app.services.itinerary_agent_service import run_itinerary_agent_flow
from app.services.operation_logs_service import start_agent_run


@pytest.fixture
def database():
    with psycopg.connect(
        host=os.environ["DATABASE_HOST"], port=os.environ["DATABASE_PORT"],
        dbname=os.environ["DATABASE_NAME"], user=os.environ["DATABASE_USER"],
        password=os.environ["DATABASE_PASSWORD"],
    ) as connection:
        connection.execute("BEGIN")
        try:
            yield connection
        finally:
            connection.rollback()


@pytest.fixture
def spool(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "SPOOL_PATH", tmp_path / "monitor.sqlite3")
    monkeypatch.setattr(monitor, "FAILURE_THRESHOLD", 2)
    monitor.initialize_spool()
    return monitor.SPOOL_PATH


def record_outage(start):
    for offset, is_up in enumerate((True, False, False, False, True)):
        monitor.record_probe_result(is_up, start + timedelta(seconds=offset * 10))


def test_monitor_debounces_outage_and_flushes_once_after_recovery(spool, database, monkeypatch):
    start = datetime.now(timezone.utc)
    record_outage(start)
    # A process restart must retain both current state and the pending event.
    monitor.initialize_spool()
    with sqlite3.connect(spool) as local:
        assert local.execute("SELECT status, failure_count FROM monitor_state").fetchone() == ("UP", 0)
        event = local.execute("SELECT monitor_event_id, flushed FROM database_outages").fetchone()
        assert event[1] == 0
        assert local.execute("SELECT count(*) FROM database_outages").fetchone()[0] == 1
    monkeypatch.setattr(monitor, "connect_database", lambda: nullcontext(database))
    monitor.flush_recovered_outages()
    monitor.flush_recovered_outages()
    incident = database.execute(
        "SELECT incident_id, status, run_id FROM incident_logs WHERE monitor_event_id = %s",
        (event[0],),
    ).fetchone()
    assert incident[1:] == ("RESOLVED", None)
    recovery = database.execute(
        "SELECT status, duration_ms, run_id FROM recovery_logs WHERE incident_id = %s",
        (incident[0],),
    ).fetchall()
    assert recovery == [("RECOVERED", 30000, None)]
    with sqlite3.connect(spool) as local:
        assert local.execute("SELECT flushed FROM database_outages").fetchone()[0] == 1


def test_monitor_ignores_one_transient_failure_and_handles_down_at_startup(spool):
    start = datetime.now(timezone.utc)
    monitor.record_probe_result(False, start)
    monitor.record_probe_result(True, start + timedelta(seconds=1))
    with sqlite3.connect(spool) as local:
        assert local.execute("SELECT count(*) FROM database_outages").fetchone()[0] == 0
    record_outage(start + timedelta(seconds=2))
    assert monitor.parse_timestamp("2026-11-06T00:00:00").tzinfo == timezone.utc
    assert monitor.parse_timestamp("2026-11-06T00:00:00+09:00").utcoffset() == timedelta(hours=9)


def test_probe_reports_database_error_without_leaking_exception(monkeypatch):
    def unavailable():
        raise psycopg.OperationalError("private database detail")
    monkeypatch.setattr(monitor, "connect_database", unavailable)
    assert monitor.probe_database() is False


@pytest.mark.parametrize("error_type", [psycopg.OperationalError, sqlite3.OperationalError])
def test_monitor_iteration_keeps_spool_errors_from_stopping_monitor(
    monkeypatch, caplog, error_type
):
    observed_at = datetime.now(timezone.utc)
    monkeypatch.setattr(monitor, "utc_now", lambda: observed_at)
    monkeypatch.setattr(monitor, "probe_database", lambda: True)
    monkeypatch.setattr(monitor, "record_probe_result", lambda is_up, at: None)

    def spool_is_temporarily_locked():
        raise error_type("private storage detail")

    monkeypatch.setattr(monitor, "flush_recovered_outages", spool_is_temporarily_locked)

    assert monitor.run_monitor_iteration() is True
    assert "remain in the local spool" in caplog.text
    assert "private storage detail" not in caplog.text


def test_monitor_preserves_spooled_outage_when_database_flush_fails(spool, monkeypatch):
    start = datetime.now(timezone.utc)
    record_outage(start)
    with sqlite3.connect(spool) as local:
        event_id, flushed = local.execute(
            "SELECT monitor_event_id, flushed FROM database_outages"
        ).fetchone()
    assert flushed == 0

    def database_is_temporarily_unavailable():
        raise psycopg.OperationalError("private database detail")

    monkeypatch.setattr(monitor, "connect_database", database_is_temporarily_unavailable)
    with pytest.raises(psycopg.OperationalError):
        monitor.flush_recovered_outages()
    with sqlite3.connect(spool) as local:
        assert local.execute(
            "SELECT flushed FROM database_outages WHERE monitor_event_id = ?",
            (event_id,),
        ).fetchone()[0] == 0


@pytest.mark.parametrize("error,stage,source,retryable", [
    (psycopg.OperationalError("private"), "queue", "DATABASE", True),
    (ValueError("invalid"), "queue", "UNKNOWN", False),
    (ValueError("invalid"), "langgraph_execution", "LANGGRAPH", False),
])
def test_retry_policy_classifies_failures_without_raw_messages(error, stage, source, retryable):
    decision = policies.classify_worker_error(error, stage)
    assert decision.error_source == source
    assert decision.retryable is retryable
    assert str(error) not in decision.safe_message


def test_retry_policy_respects_external_and_llm_retry_contracts():
    class ApiError(Exception):
        error_code = "culture_api_timeout"
        retryable = True
    assert policies.classify_worker_error(ApiError(), "events").retryable
    error = ApiError()
    error.retryable = False
    assert not policies.classify_worker_error(error, "events").retryable
    error.error_code = "LLM_RATE_LIMIT"
    error.retryable = True
    assert not policies.classify_worker_error(error, "langgraph_execution").retryable
    error.error_code = "OTHER"
    assert policies.classify_worker_error(error, "langgraph_execution").retryable
    error.error_code = "DATABASE_OPERATION_FAILED"
    assert policies.classify_worker_error(error, "queue").error_source == "DATABASE"
    assert [policies.retry_delay_seconds(n) for n in (1, 2, 3, 20)] == [2, 4, 8, 30]


def test_unexpected_langgraph_runtime_failure_uses_bounded_worker_recovery():
    class GraphExecutionError(RuntimeError):
        error_code = "LANGGRAPH_EXECUTION_FAILED"

    decision = policies.classify_worker_error(
        GraphExecutionError("synthetic graph runtime failure"),
        "langgraph_execution",
    )

    assert decision.retryable is True
    assert decision.error_source == "LANGGRAPH"
    assert decision.error_code == "LANGGRAPH_EXECUTION_FAILED"
    assert decision.recovery_action == "retry_langgraph_worker"
    assert "synthetic graph runtime failure" not in decision.safe_message


def initial_execution(connection):
    visitor = uuid4()
    group, run = repository.create_initial_execution(
        connection, visitor, datetime.now(timezone.utc) + timedelta(hours=1),
        dict(origin_stop_id="MANGWON", destination_stop_id="YEOUIDO",
             service_date=date(2026, 11, 21), preferred_departure_time="11:42",
             companion_type="family", activity_type="exhibition_performance"),
    )
    return visitor, group, run


def test_langgraph_runtime_failure_is_requeued_then_worker_recovers(database, monkeypatch):
    _, _, run_id = initial_execution(database)
    trip = TripEstimate(
        from_stop_id="MANGWON",
        to_stop_id="YEOUIDO",
        departure_time="11:42",
        travel_minutes=26,
        estimated_arrival_time="12:08",
        arrival_day_offset=0,
        arrival_time_source="calculated",
        stop_call_status="verified",
    )
    timetable = TimetableSnapshot(
        dataset_id="FT-02-TEST-TIMETABLE",
        timezone="Asia/Seoul",
        source_url="https://example.test/timetable",
        effective_from="2026-01-01",
        effective_until=None,
        operating_days_status="daily",
        separate_operational_notices_reviewed=True,
        operation_policy_confirmed=True,
        weather_disruption_notice="Temporary suspension may occur due to weather.",
        stops=(Stop("MANGWON", "망원", 1), Stop("YEOUIDO", "여의도", 2)),
        trip_estimates=(trip,),
    )
    candidate = {
        "candidate_id": "event:ft02-recovery",
        "event_id": "event:ft02-recovery",
        "candidate_type": "EVENT",
        "title": "FT-02 검증용 합성 전시",
        "place": "테스트 전시장",
        "address": "서울시 테스트 주소",
        "latitude": 37.52,
        "longitude": 126.93,
        "detail_url": "https://example.test/event",
        "event_start_time": "13:00",
        "event_end_time": "15:00",
        "planned_visit_start_time": "13:00",
        "event_is_active_on_service_date": True,
        "date_evidence": ["Synthetic FT-02 test date"],
        "review_status": "verified",
        "eligibility_status": "POSSIBLE",
        "travel_minutes": 15,
        "evidence_ids": ["synthetic-event-catalog:ft02"],
    }
    destination_filter = {
        "status": "applied_straight_line_distance",
        "radius_km": 5,
    }
    monkeypatch.setattr(worker, "create_database_connection", lambda: nullcontext(database))
    monkeypatch.setattr(
        worker,
        "get_departure_estimate",
        lambda *_args: (timetable, trip),
    )
    monkeypatch.setattr(worker, "fetch_events_for_date", lambda *_args, **_kwargs: [{}])
    monkeypatch.setattr(
        worker,
        "filter_event_rows_for_destination",
        lambda rows, _stop_id: (rows, destination_filter),
    )
    monkeypatch.setattr(worker, "build_candidates", lambda *_args: [candidate])
    monkeypatch.setattr(
        worker,
        "filter_candidates_after_arrival",
        lambda candidates, _arrival: candidates,
    )
    monkeypatch.setattr(worker, "create_openai_agent_models", create_demo_agent_models)

    graph_attempts = 0

    class TransientGraphRuntimeError(RuntimeError):
        error_code = "LANGGRAPH_EXECUTION_FAILED"

    def fail_graph_once_then_run(*args, **kwargs):
        nonlocal graph_attempts
        graph_attempts += 1
        if graph_attempts == 1:
            raise TransientGraphRuntimeError("synthetic LangGraph runtime interruption")
        return run_itinerary_agent_flow(*args, **kwargs)

    monkeypatch.setattr(worker, "run_itinerary_agent_flow", fail_graph_once_then_run)

    first_attempt = repository.claim_next_execution(database, worker.WORKER_ID)
    assert first_attempt["run_id"] == run_id
    assert worker._process_claimed_execution(first_attempt) is True

    first_state = database.execute(
        "SELECT status, queue_status, worker_attempt_count FROM itinerary_executions WHERE run_id = %s",
        (run_id,),
    ).fetchone()
    incident = database.execute(
        "SELECT incident_id, status, error_source, error_code FROM incident_logs WHERE run_id = %s",
        (run_id,),
    ).fetchone()
    assert first_state == ("RUNNING", "QUEUED", 1)
    assert incident[1:] == ("RECOVERING", "LANGGRAPH", "LANGGRAPH_EXECUTION_FAILED")

    database.execute(
        "UPDATE itinerary_executions SET next_retry_at = now() - interval '1 second' WHERE run_id = %s",
        (run_id,),
    )
    retry_attempt = repository.claim_next_execution(database, worker.WORKER_ID)
    assert retry_attempt["run_id"] == run_id
    assert retry_attempt["worker_attempt_count"] == 2
    assert worker._process_claimed_execution(retry_attempt) is True

    final_state = database.execute(
        "SELECT status, queue_status, worker_attempt_count FROM itinerary_executions WHERE run_id = %s",
        (run_id,),
    ).fetchone()
    agent_run = database.execute(
        "SELECT status FROM agent_runs WHERE run_id = %s",
        (run_id,),
    ).fetchone()
    incident_state = database.execute(
        "SELECT status FROM incident_logs WHERE incident_id = %s",
        (incident[0],),
    ).fetchone()
    recovery_state = database.execute(
        "SELECT run_id, attempt_number, status, recovery_action FROM recovery_logs WHERE incident_id = %s",
        (incident[0],),
    ).fetchone()
    itinerary_count = database.execute(
        "SELECT count(*) FROM itinerary_versions WHERE execution_id = %s",
        (run_id,),
    ).fetchone()[0]

    assert graph_attempts == 2
    assert final_state == ("SUCCESS", "DONE", 2)
    assert agent_run == ("SUCCESS",)
    assert incident_state == ("RESOLVED",)
    assert recovery_state == (run_id, 1, "RECOVERED", "retry_langgraph_worker")
    assert itinerary_count == 1


def test_queue_retry_ownership_expired_lease_and_session_isolation(database):
    visitor, group, run = initial_execution(database)
    assert repository.get_execution_for_session(database, run, uuid4()) is None
    assert repository.get_execution_for_session(database, run, visitor)["status"] == "RUNNING"
    claimed = repository.claim_next_execution(database, "worker-a")
    assert claimed["run_id"] == run and not claimed["lease_expired"]
    assert not repository.renew_execution_lease(database, run, "wrong-owner")
    assert repository.renew_execution_lease(database, run, "worker-a")
    assert not controller.schedule_execution_retry(database, run, "wrong-owner", 30)
    assert controller.schedule_execution_retry(database, run, "worker-a", 30)
    assert repository.claim_next_execution(database, "worker-b") is None
    database.execute("UPDATE itinerary_executions SET next_retry_at = now() - interval '1 second' WHERE run_id = %s", (run,))
    assert repository.claim_next_execution(database, "worker-b")["worker_attempt_count"] == 2
    database.execute("UPDATE itinerary_executions SET worker_lease_until = now() - interval '1 second' WHERE run_id = %s", (run,))
    assert repository.claim_next_execution(database, "worker-c")["lease_expired"]
    with pytest.raises(repository.GenerationAlreadyRunning):
        repository.create_regeneration_execution(database, visitor, group)
    repository.complete_execution_without_result(database, run, "NO_MATCH", "No candidate")
    result = repository.get_execution_for_session(database, run, visitor)
    assert result["status"] == "NO_RESULT" and result["reason"]["code"] == "NO_MATCH"
    next_run, index = repository.create_regeneration_execution(database, visitor, group)
    assert index == 1
    assert repository.create_regeneration_execution(database, visitor, group) == (next_run, 1)
    repository.fail_execution(database, next_run, "TEST_FAILURE", "safe failure", False)
    assert repository.get_execution_for_session(database, next_run, visitor)["error"]["retryable"] is False
    with pytest.raises(repository.ItineraryGroupNotFound):
        repository.create_regeneration_execution(database, uuid4(), group)
    database.execute("UPDATE itinerary_groups SET regeneration_count = 2 WHERE group_id = %s", (group,))
    with pytest.raises(repository.RegenerationLimitReached):
        repository.create_regeneration_execution(database, visitor, group)


def test_recovery_attempts_share_incident_and_finish_terminally(database):
    run = start_agent_run(database)
    fields = dict(run_id=run, error_source="DATABASE", error_code="DB_TEST",
                  error_message="Safe test error", failed_step_name="queue",
                  recovery_action="reconnect")
    incident = controller.start_run_recovery(database, **fields)
    assert controller.start_run_recovery(database, **fields) == incident
    rows = database.execute("SELECT attempt_number, status FROM recovery_logs WHERE incident_id = %s ORDER BY attempt_number", (incident,)).fetchall()
    assert rows == [(1, "FAILED"), (2, "RUNNING")]
    controller.finish_run_recoveries(database, run, "RECOVERED")
    assert database.execute("SELECT status FROM incident_logs WHERE incident_id = %s", (incident,)).fetchone()[0] == "RESOLVED"
    fields.pop("recovery_action")
    terminal = controller.record_terminal_run_failure(database, **fields)
    assert terminal != incident
    controller.resume_agent_run(database, run)
    controller.close_abandoned_step_logs(database, run)


def test_password_login_session_and_logout(monkeypatch):
    monkeypatch.setenv("SITE_ACCESS_PASSWORDS", "test-local-password")
    with TestClient(app) as client:
        assert client.get("/api/auth/session").json() == {"authenticated": False}
        assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
        assert client.post("/api/auth/login", json={"password": "test-local-password"}).status_code == 200
        assert client.get("/api/auth/session").json() == {"authenticated": True}
        assert client.post("/api/auth/logout").json() == {"authenticated": False}
        assert client.get("/api/auth/session").json() == {"authenticated": False}
