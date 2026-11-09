import logging
import json
import os
import re
import socket
import time
from datetime import date, datetime, time as local_time, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg import Connection

from app.agents.itinerary_graph import build_itinerary_graph
from app.agents.llm_client import create_openai_agent_models
from app.core.database import create_database_connection
from app.integrations.seoul_culture_api import fetch_event_page
from app.repositories.itinerary_repository import claim_next_execution
from app.repositories.critic_review_repository import save_critic_reviews
from app.repositories.place_repository import list_active_recommendation_places
from app.repositories.timetable_repository import get_timetable_snapshot
from app.repositories.operation_logs_repository import update_run_component_log
from app.repositories.itinerary_repository import renew_execution_lease
from app.recovery.controller import (
    close_abandoned_step_logs,
    finish_run_recoveries,
    record_terminal_run_failure,
    resume_agent_run,
    schedule_execution_retry,
    start_run_recovery,
)
from app.recovery.policies import (
    MAX_WORKER_ATTEMPTS,
    classify_worker_error,
    retry_delay_seconds,
)
from app.schemas.itinerary import (
    ItineraryItemResponse,
    ItineraryResponse,
    LocationResponse,
    SourceReference,
    StopReference,
)
from app.services.itinerary_agent_service import run_itinerary_agent_flow
from app.services.itinerary_request_service import (
    finish_execution_as_failed,
    finish_execution_as_no_result,
    finish_execution_with_itinerary,
)
from app.services.operation_logs_service import record_api_call
from app.services.timetable_service import get_departure_estimate


POLL_INTERVAL_SECONDS = 1.0
WORKER_ID = f"{socket.gethostname()}-itinerary"
LOGGER = logging.getLogger("itinerary_worker")
SEOUL_TIMEZONE = ZoneInfo("Asia/Seoul")
API_PAGE_SIZE = 1000
MAX_RECOMMENDATION_CANDIDATES = 20
LANGGRAPH_HEALTH_PORT = int(os.environ.get('LANGGRAPH_HEALTH_PORT', '8090'))
WORKER_LEASE_SECONDS = 90
WORKER_LEASE_HEARTBEAT_SECONDS = 20
REQUIRED_LANGGRAPH_NODES = {
    'trip_mobility_agent',
    'event_explanation_agent',
    'schedule_critic_agent',
    'result_validator',
}


def compile_langgraph_for_readiness() -> None:
    """Compile the graph topology without invoking any node or external API."""
    compiled_graph = build_itinerary_graph(agent_models=None)
    graph_nodes = set(compiled_graph.get_graph().nodes)
    if not REQUIRED_LANGGRAPH_NODES.issubset(graph_nodes):
        raise RuntimeError('The compiled LangGraph is missing required nodes.')


def start_langgraph_health_server() -> ThreadingHTTPServer:
    """Expose an internal liveness/readiness signal for the admin dashboard."""
    class LangGraphHealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != '/health':
                self.send_error(404)
                return
            response_body = json.dumps(
                {'status': 'ready', 'checks': {'langgraph': 'ok'}},
            ).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    health_server = ThreadingHTTPServer(
        ('0.0.0.0', LANGGRAPH_HEALTH_PORT),
        LangGraphHealthHandler,
    )
    health_server.daemon_threads = True
    Thread(
        target=health_server.serve_forever,
        name='langgraph-health-server',
        daemon=True,
    ).start()
    return health_server


class ExecutionLeaseHeartbeat:
    """Renew a live worker lease while bounded external calls are in progress."""

    def __init__(self, run_id: UUID) -> None:
        self.run_id = run_id
        self.stopped = Event()
        self.thread = Thread(
            target=self._renew_until_stopped,
            name=f'lease-heartbeat-{run_id}',
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()
        self.thread.join(timeout=2)

    def _renew_until_stopped(self) -> None:
        while not self.stopped.wait(WORKER_LEASE_HEARTBEAT_SECONDS):
            try:
                with create_database_connection() as connection:
                    renewed = renew_execution_lease(
                        connection,
                        self.run_id,
                        WORKER_ID,
                        WORKER_LEASE_SECONDS,
                    )
                if not renewed:
                    LOGGER.warning('Worker lease is no longer owned for run %s.', self.run_id)
                    return
            except Exception:
                LOGGER.warning('Worker lease renewal failed for run %s.', self.run_id)


def process_next_execution() -> bool:
    with create_database_connection() as connection:
        execution = claim_next_execution(
            connection,
            WORKER_ID,
            lease_seconds=WORKER_LEASE_SECONDS,
        )
    if execution is None:
        return False

    with create_database_connection() as connection:
        update_run_component_log(
            connection,
            execution['run_id'],
            'POSTGRESQL',
            'UP',
            'DATABASE_CONNECTION_CONFIRMED',
        )
        update_run_component_log(
            connection,
            execution['run_id'],
            'OPERATIONAL_LOG_SCHEMA',
            'UP',
            'OPERATIONAL_LOG_SCHEMA_CONFIRMED',
        )

    if execution['worker_attempt_count'] > MAX_WORKER_ATTEMPTS:
        with create_database_connection() as connection:
            error_code = 'WORKER_RETRY_LIMIT_EXCEEDED'
            record_terminal_run_failure(
                connection,
                execution['run_id'],
                'UNKNOWN',
                error_code,
                'The worker exceeded its bounded execution attempts.',
                'recommendation_worker',
                severity='CRITICAL',
            )
            finish_execution_as_failed(
                connection,
                execution['run_id'],
                error_code,
                '추천 작업을 복구하지 못했습니다. 잠시 후 다시 요청해 주세요.',
                retryable=False,
            )
        LOGGER.error('Worker retry limit reached for run %s.', execution['run_id'])
        return True

    if execution['worker_attempt_count'] > 1:
        with create_database_connection() as connection:
            close_abandoned_step_logs(connection, execution['run_id'])
            resume_agent_run(connection, execution['run_id'])
    if execution['lease_expired']:
        with create_database_connection() as connection:
            start_run_recovery(
                connection,
                execution['run_id'],
                'UNKNOWN',
                'WORKER_LEASE_EXPIRED',
                'The worker stopped before completing its leased execution.',
                'recommendation_worker',
                'reclaim_expired_worker_lease',
            )

    heartbeat = ExecutionLeaseHeartbeat(execution['run_id'])
    heartbeat.start()
    try:
        return _process_claimed_execution(execution)
    finally:
        heartbeat.stop()


def _process_claimed_execution(execution: dict[str, Any]) -> bool:

    processing_stage = "timetable_lookup"
    try:
        timetable, trip = get_departure_estimate(
            execution["origin_stop_id"],
            execution["destination_stop_id"],
            execution["service_date"],
            execution["preferred_departure_time"],
        )
        processing_stage = "culture_api_lookup"
        with create_database_connection() as connection:
            events = fetch_events_for_date(
                execution["service_date"],
                connection=connection,
                run_id=execution["run_id"],
            )
        events, filter_metadata = filter_event_rows_for_destination(
            events,
            execution["destination_stop_id"],
        )
        processing_stage = "candidate_validation"
        candidates = build_candidates(events, execution["service_date"])
        trip_context = build_trip_context(
            execution,
            timetable,
            trip,
            destination_filter=filter_metadata,
        )
        candidates = filter_candidates_after_arrival(candidates, trip.estimated_arrival_time)
        if not candidates:
            processing_stage = "place_catalog_lookup"
            with create_database_connection() as connection:
                candidates, place_filter_metadata = build_nearby_place_candidates(
                    connection,
                    execution["destination_stop_id"],
                    execution["activity_type"],
                )
            filter_metadata = {
                **filter_metadata,
                "place_fallback": place_filter_metadata,
            }
        if not candidates:
            with create_database_connection() as connection:
                finish_execution_as_no_result(
                    connection,
                    execution["run_id"],
                    "NO_EVENTS_OR_NEARBY_PLACES",
                    "선택한 날짜에 조건에 맞는 행사가 없고, 도착 선착장 주변에서 선호 활동에 맞는 상설 장소도 찾지 못했습니다.",
                )
            return True
        processing_stage = "llm_configuration"
        models = create_openai_agent_models()
        processing_stage = "langgraph_execution"
        graph_started = time.perf_counter()
        with create_database_connection() as connection:
            update_run_component_log(
                connection,
                execution["run_id"],
                'LANGGRAPH',
                'RUNNING',
                'GRAPH_EXECUTION_STARTED',
                started_at=datetime.now(timezone.utc),
            )
            agent_result = run_itinerary_agent_flow(
                connection,
                models,
                trip_context,
                candidates,
                run_id=execution["run_id"],
            )
            processing_stage = 'critic_review_persistence'
            update_run_component_log(
                connection,
                execution["run_id"],
                'LANGGRAPH',
                'UP',
                'GRAPH_EXECUTION_COMPLETED',
                latency_ms=round((time.perf_counter() - graph_started) * 1000),
            )
            update_api_component_status(connection, execution["run_id"], 'LLM')
            save_critic_reviews(
                connection,
                execution["run_id"],
                agent_result.get("critic_reviews", []),
            )
        processing_stage = "itinerary_persistence"
        final_result = agent_result["final_result"]
        if final_result.get("result_type") != "eligible_candidates":
            with create_database_connection() as connection:
                finish_execution_as_no_result(
                    connection,
                    execution["run_id"],
                    "NO_MATCHING_ITINERARY",
                    "조건에 맞는 나들이 코스를 구성하지 못했습니다. 날짜나 조건을 바꿔 다시 시도해 주세요.",
                )
            return True

        itinerary = build_itinerary(execution, timetable, trip, final_result, candidates)
        snapshot = {
            "service_date": execution["service_date"].isoformat(),
            "timetable_dataset_id": timetable.dataset_id,
            "timetable_source_url": timetable.source_url,
            "timetable_assumption": "published service operates every calendar day; temporary weather suspension remains possible",
            "candidate_source": (
                "Seoul Open API aiViewCulture"
                if candidates[0].get("candidate_type") == "EVENT"
                else "PostgreSQL places catalog"
            ),
            "candidate_type": candidates[0].get("candidate_type", "EVENT"),
            "recommendation_candidates": candidates,
            "destination_filter": filter_metadata,
        }
        with create_database_connection() as connection:
            finish_execution_with_itinerary(connection, execution, itinerary, snapshot)
    except Exception as error:
        decision = classify_worker_error(error, processing_stage)
        safe_message = (
            "행사 정보를 일시적으로 불러오지 못했습니다. 잠시 후 다시 시도합니다."
            if decision.error_source == 'EXTERNAL_API'
            else "코스를 생성하지 못했습니다. 조건을 확인하고 다시 시도해 주세요."
        )
        with create_database_connection() as connection:
            if processing_stage == 'langgraph_execution':
                update_run_component_log(
                    connection,
                    execution["run_id"],
                    'LANGGRAPH',
                    'DOWN',
                    'GRAPH_EXECUTION_FAILED',
                )
                update_api_component_status(connection, execution["run_id"], 'LLM')
            if (
                decision.retryable
                and execution['worker_attempt_count'] < MAX_WORKER_ATTEMPTS
            ):
                close_abandoned_step_logs(connection, execution['run_id'])
                resume_agent_run(connection, execution['run_id'])
                start_run_recovery(
                    connection,
                    execution['run_id'],
                    decision.error_source,
                    decision.error_code,
                    decision.safe_message,
                    processing_stage,
                    decision.recovery_action,
                )
                delay_seconds = retry_delay_seconds(execution['worker_attempt_count'])
                queued = schedule_execution_retry(
                    connection,
                    execution['run_id'],
                    WORKER_ID,
                    delay_seconds,
                )
                if queued:
                    LOGGER.warning(
                        'Scheduled worker retry %s/%s for run %s after %ss (%s).',
                        execution['worker_attempt_count'] + 1,
                        MAX_WORKER_ATTEMPTS,
                        execution['run_id'],
                        delay_seconds,
                        decision.error_code,
                    )
                    return True
                finish_run_recoveries(connection, execution['run_id'], 'FAILED')
            record_terminal_run_failure(
                connection,
                execution['run_id'],
                decision.error_source,
                decision.error_code,
                decision.safe_message,
                processing_stage,
            )
            finish_execution_as_failed(
                connection,
                execution["run_id"],
                decision.error_code,
                "행사 API 오류가 반복되거나 복구할 수 없어 코스를 생성하지 못했습니다."
                if decision.error_source == 'EXTERNAL_API'
                else "코스를 생성하지 못했습니다. 조건을 확인하고 다시 시도해 주세요.",
                retryable=decision.retryable,
            )
        LOGGER.warning(
            "Itinerary run %s failed with %s (%s).",
            execution["run_id"],
            decision.error_code,
            f"{processing_stage}:{type(error).__name__}",
        )
    return True


def update_api_component_status(
    connection: Connection,
    run_id: UUID,
    api_type: str,
) -> None:
    component_name = 'LLM_API' if api_type == 'LLM' else 'SEOUL_CULTURAL_EVENTS'
    summary = connection.execute(
        """SELECT count(*)::integer AS total,
                  count(*) FILTER (WHERE success)::integer AS successful,
                  coalesce(sum(latency_ms), 0)::integer AS latency_ms,
                  min(called_at) AS started_at
             FROM api_call_logs
            WHERE run_id = %s AND api_type = %s""",
        (run_id, api_type),
    ).fetchone()
    total = int(summary[0])
    successful = int(summary[1])
    if total == 0:
        status = 'NOT_RUN'
        reason_code = 'NO_API_CALL_RECORDED'
    elif successful == total:
        status = 'UP'
        reason_code = 'ALL_API_CALLS_SUCCEEDED'
    elif successful == 0:
        status = 'DOWN'
        reason_code = 'ALL_API_CALLS_FAILED'
    else:
        status = 'DEGRADED'
        reason_code = 'SOME_API_CALLS_FAILED'
    update_run_component_log(
        connection,
        run_id,
        component_name,
        status,
        reason_code,
        latency_ms=int(summary[2]) if total else None,
        started_at=summary[3],
    )


def fetch_events_for_date(
    service_date: date,
    connection: Connection | None = None,
    run_id: UUID | None = None,
) -> list[dict[str, Any]]:
    component_started = time.perf_counter()
    component_started_at = datetime.now(timezone.utc)
    if connection is not None and run_id is not None:
        update_run_component_log(
            connection,
            run_id,
            'SEOUL_CULTURAL_EVENTS',
            'RUNNING',
            'API_REQUEST_STARTED',
            started_at=component_started_at,
        )
    api_key = os.environ.get("SEOUL_CULTURE_API_KEY", "").strip()
    if not api_key:
        if connection is not None and run_id is not None:
            update_run_component_log(
                connection,
                run_id,
                'SEOUL_CULTURAL_EVENTS',
                'DOWN',
                'API_NOT_CONFIGURED',
                latency_ms=round((time.perf_counter() - component_started) * 1000),
            )
        raise RuntimeError("culture_api_not_configured")

    def fetch_page(start_index: int, end_index: int) -> tuple[int, tuple[dict[str, object], ...]]:
        request_started = time.perf_counter()
        try:
            page_result = fetch_event_page(api_key, start_index, end_index)
        except Exception as error:
            if connection is not None and run_id is not None:
                record_api_call(
                    connection,
                    run_id,
                    provider='seoul_openapi',
                    api_type='CULTURE',
                    http_status=getattr(error, 'http_status', None),
                    latency_ms=max(0, round((time.perf_counter() - request_started) * 1000)),
                    success=False,
                    error_source='EXTERNAL_API',
                    error_code=getattr(error, 'error_code', 'CULTURE_API_REQUEST_FAILED'),
                )
            raise
        if connection is not None and run_id is not None:
            record_api_call(
                connection,
                run_id,
                provider='seoul_openapi',
                api_type='CULTURE',
                http_status=200,
                latency_ms=max(0, round((time.perf_counter() - request_started) * 1000)),
                success=True,
            )
        return page_result

    try:
        total_count, rows = fetch_page(1, API_PAGE_SIZE)
        collected_rows = list(rows)
        while len(collected_rows) < total_count:
            start_index = len(collected_rows) + 1
            _, page_rows = fetch_page(
                start_index,
                min(start_index + API_PAGE_SIZE - 1, total_count),
            )
            if not page_rows:
                raise RuntimeError("culture_api_incomplete_response")
            collected_rows.extend(page_rows)
    except Exception as error:
        if connection is not None and run_id is not None:
            update_run_component_log(
                connection,
                run_id,
                'SEOUL_CULTURAL_EVENTS',
                'DOWN',
                getattr(error, 'error_code', 'API_REQUEST_FAILED').upper(),
                latency_ms=round((time.perf_counter() - component_started) * 1000),
            )
            update_api_component_status(connection, run_id, 'CULTURE')
        raise

    if connection is not None and run_id is not None:
        update_run_component_log(
            connection,
            run_id,
            'SEOUL_CULTURAL_EVENTS',
            'UP',
            'API_RESPONSE_RECEIVED',
            latency_ms=round((time.perf_counter() - component_started) * 1000),
        )
        update_api_component_status(connection, run_id, 'CULTURE')
    return [row for row in collected_rows if event_matches_date(row, service_date)]


def event_matches_date(row: dict[str, Any], service_date: date) -> bool:
    start_date = parse_api_date(row.get("STRTDATE"))
    end_date = parse_api_date(row.get("END_DATE"))
    if start_date is None or end_date is None:
        return False
    # Drop malformed/open-ended API records (the live feed currently includes
    # spans such as 2026–2626 and 2023–2099) rather than treating them as valid
    # events for every selectable date. This is a conservative service rule.
    if end_date < start_date or (end_date - start_date).days > 730:
        return False
    return start_date <= service_date <= end_date


def parse_api_date(value: Any) -> date | None:
    if value is None:
        return None
    normalized = str(value).strip()
    for date_format in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(normalized[:10], date_format).date()
        except ValueError:
            continue
    return None


def build_candidates(rows: list[dict[str, Any]], service_date: date) -> list[dict[str, Any]]:
    candidates = []
    for index, row in enumerate(rows):
        title = str(row.get("TITLE") or "").strip()
        detail_url = str(row.get("HMPG_ADDR") or row.get("ORG_LINK") or "").strip()
        if not title or not detail_url:
            continue
        # These IDs are request-local references, not official Seoul event IDs.
        event_id = f"candidate-{index + 1}"
        event_start_time, event_end_time = parse_event_time_window(
            row.get("PRO_TIME"),
            service_date,
        )
        category = str(row.get("CODENAME") or "").strip() or None
        candidates.append({
            "candidate_id": event_id,
            "candidate_type": "EVENT",
            "event_id": event_id,
            "title": title,
            "place": str(row.get("PLACE") or "").strip() or None,
            "category": category,
            "start_date": str(row.get("STRTDATE") or ""),
            "end_date": str(row.get("END_DATE") or ""),
            "event_date_text": str(row.get("DATE") or ""),
            "program_time_text": str(row.get("PRO_TIME") or "").strip() or None,
            "event_start_time": event_start_time,
            "event_end_time": event_end_time,
            "event_time_kind": (
                "opening_hours"
                if event_end_time is not None
                and category is not None
                and ("전시" in category or "미술" in category)
                else "session"
            ),
            "planned_visit_start_time": None,
            "event_time_status": (
                "parsed_for_service_date"
                if event_start_time is not None
                else "unknown_or_ambiguous"
            ),
            "event_is_active_on_service_date": True,
            "candidate_is_valid_for_service_date": True,
            "date_evidence": [
                f"STRTDATE:{row.get('STRTDATE')}",
                f"END_DATE:{row.get('END_DATE')}",
            ],
            "detail_url": detail_url,
            "latitude": parse_coordinate(row.get("LAT"), -90, 90),
            "longitude": parse_coordinate(row.get("LOT"), -180, 180),
            "review_status": "verified",
            "eligibility_status": "POSSIBLE",
            "evidence_ids": [f"culture-api:{event_id}"],
        })
    return candidates


PLACE_CATEGORIES_BY_ACTIVITY: dict[str, tuple[str, ...]] = {
    "exhibition_performance": ("MUSEUM", "ART_GALLERY", "EXHIBITION_HALL", "CULTURAL_SPACE"),
    "culture_event": ("MUSEUM", "ART_GALLERY", "EXHIBITION_HALL", "CULTURAL_SPACE"),
    "walk_healing": ("PARK", "BOTANICAL_GARDEN"),
    # The place catalog has no food or cafe categories, so don't mislabel an
    # unrelated venue as a match for this preference.
    "food_cafe": (),
}


def build_nearby_place_candidates(
    connection: Any,
    destination_stop_id: str,
    activity_type: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    categories = PLACE_CATEGORIES_BY_ACTIVITY.get(activity_type, ())
    if not categories:
        return [], {
            "status": "no_catalog_categories_for_activity",
            "activity_type": activity_type,
            "destination_stop_id": destination_stop_id,
        }

    coordinates = load_stop_coordinates()
    stop_coordinates = coordinates.get(destination_stop_id.upper())
    if not isinstance(stop_coordinates, (list, tuple)) or len(stop_coordinates) != 2:
        return [], {
            "status": "not_applied_missing_destination_stop_coordinates",
            "destination_stop_id": destination_stop_id,
        }
    try:
        latitude, longitude = float(stop_coordinates[0]), float(stop_coordinates[1])
        radius_km = float(os.environ.get("EVENT_DESTINATION_RADIUS_KM", "5"))
    except (TypeError, ValueError):
        return [], {
            "status": "failed_invalid_destination_stop_coordinates",
            "destination_stop_id": destination_stop_id,
        }
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180 and radius_km > 0):
        return [], {
            "status": "failed_invalid_destination_stop_coordinates",
            "destination_stop_id": destination_stop_id,
        }

    nearby_places = []
    for place in list_active_recommendation_places(connection, categories):
        distance_km = calculate_distance_km(
            latitude,
            longitude,
            float(place.latitude),
            float(place.longitude),
        )
        if distance_km > radius_km:
            continue
        address = place.road_address or place.lot_address
        evidence_id = f"place-catalog:{place.place_id}"
        nearby_places.append({
            "candidate_id": f"place:{place.place_id}",
            "candidate_type": "PLACE",
            "title": place.name,
            "place": place.name,
            "address": address,
            "category": place.category,
            "description": place.description,
            "facilities": place.facilities,
            "opening_hours": place.opening_hours,
            "closed_days": place.closed_days,
            "admission_info": place.admission_info,
            "latitude": float(place.latitude),
            "longitude": float(place.longitude),
            "destination_distance_km": round(distance_km, 1),
            "detail_url": place.source_url or place.website,
            "event_start_time": None,
            "event_end_time": None,
            "planned_visit_start_time": None,
            "candidate_is_valid_for_service_date": True,
            "date_evidence": [
                "place_catalog:active_permanent_place",
                "place_catalog:selected_date_does_not_filter_place_existence",
            ],
            "review_status": "verified",
            "eligibility_status": "POSSIBLE",
            "evidence_ids": [evidence_id],
        })

    nearby_places.sort(key=lambda item: (item["destination_distance_km"], item["title"]))
    return nearby_places[:MAX_RECOMMENDATION_CANDIDATES], {
        "status": "applied_straight_line_distance",
        "destination_stop_id": destination_stop_id,
        "radius_km": radius_km,
        "categories": list(categories),
        "candidate_count": min(len(nearby_places), MAX_RECOMMENDATION_CANDIDATES),
        "distance_semantics": "straight_line_not_route_distance",
    }


def load_stop_coordinates() -> dict[str, Any]:
    import json

    raw_coordinates = os.environ.get("FERRY_STOP_COORDINATES_JSON", "").strip()
    if not raw_coordinates:
        return {}
    try:
        coordinates = json.loads(raw_coordinates)
    except json.JSONDecodeError:
        return {}
    return coordinates if isinstance(coordinates, dict) else {}


def calculate_distance_km(
    from_latitude: float,
    from_longitude: float,
    to_latitude: float,
    to_longitude: float,
) -> float:
    import math

    latitude_delta = math.radians(to_latitude - from_latitude)
    longitude_delta = math.radians(to_longitude - from_longitude)
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(math.radians(from_latitude))
        * math.cos(math.radians(to_latitude))
        * math.sin(longitude_delta / 2) ** 2
    )
    return 6371 * 2 * math.asin(math.sqrt(haversine))


def parse_event_time_window(
    value: Any,
    service_date: date,
) -> tuple[str | None, str | None]:
    """Parse only an unambiguous time from the API's free-form PRO_TIME field."""
    if value is None:
        return None, None
    text = str(value).strip()
    if not text:
        return None, None

    time_pattern = r"(?P<hour>\d{1,2}):(?P<minute>\d{2})"
    date_marker = rf"{service_date.month}\s*[.]\s*{service_date.day}"
    date_match = re.search(
        rf"{date_marker}[^0-9]{{0,12}}(?P<start>\d{{1,2}}:\d{{2}})"
        rf"(?:\s*[~-]\s*(?P<end>\d{{1,2}}:\d{{2}}))?",
        text,
    )
    if date_match:
        return date_match.group("start"), date_match.group("end")

    # A single start/end range is common for exhibitions, where it describes
    # the public opening window rather than a one-time performance. Keep it as
    # an interval so the arrival filter can admit visits while it is open.
    interval_pattern = (
        rf"(?<!\d)(?P<start>\d{{1,2}}:\d{{2}})\s*[~～–-]\s*"
        rf"(?P<end>\d{{1,2}}:\d{{2}})(?!\d)"
    )
    intervals = list(re.finditer(interval_pattern, text))
    time_matches = list(re.finditer(time_pattern, text))
    if len(intervals) == 1 and len(time_matches) == 2:
        start = intervals[0].group("start")
        end = intervals[0].group("end")
        try:
            local_time.fromisoformat(start)
            local_time.fromisoformat(end)
        except ValueError:
            return None, None
        return start, end

    # A single time without a date is safe to reuse for the selected date.
    if len(time_matches) != 1:
        return None, None
    start = time_matches[0].group(0)
    tail = text[time_matches[0].end():]
    end_match = re.match(r"\s*[~-]\s*(\d{1,2}:\d{2})", tail)
    return start, end_match.group(1) if end_match else None


def filter_candidates_after_arrival(
    candidates: list[dict[str, Any]],
    estimated_arrival_time: str,
) -> list[dict[str, Any]]:
    try:
        arrival = local_time.fromisoformat(estimated_arrival_time)
    except ValueError:
        return []
    eligible_candidates = []
    for candidate in candidates:
        event_start_text = candidate.get("event_start_time")
        if not event_start_text:
            continue
        try:
            event_start = local_time.fromisoformat(event_start_text)
        except (TypeError, ValueError):
            continue

        planned_visit_start = event_start
        if event_start < arrival:
            event_end_text = candidate.get("event_end_time")
            if candidate.get("event_time_kind") != "opening_hours" or not event_end_text:
                continue
            try:
                event_end = local_time.fromisoformat(event_end_text)
            except (TypeError, ValueError):
                continue
            if event_end <= arrival:
                continue
            planned_visit_start = arrival

        eligible_candidates.append({
            **candidate,
            "planned_visit_start_time": planned_visit_start.strftime("%H:%M"),
        })
        if len(eligible_candidates) >= MAX_RECOMMENDATION_CANDIDATES:
            break
    return eligible_candidates


def filter_event_rows_for_destination(
    rows: list[dict[str, Any]],
    destination_stop_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Use straight-line distance only when verified stop coordinates are supplied."""
    import json
    import math

    raw_coordinates = os.environ.get("FERRY_STOP_COORDINATES_JSON", "").strip()
    if not raw_coordinates:
        return (
            [row for row in rows
             if parse_coordinate(row.get("LAT"), -90, 90) is not None
             and parse_coordinate(row.get("LOT"), -180, 180) is not None],
            {
                "status": "not_applied_missing_destination_stop_coordinates",
                "destination_stop_id": destination_stop_id,
            },
        )
    try:
        coordinates = json.loads(raw_coordinates)
        latitude, longitude = coordinates[destination_stop_id.upper()]
        latitude = float(latitude)
        longitude = float(longitude)
        radius_km = float(os.environ.get("EVENT_DESTINATION_RADIUS_KM", "5"))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return [], {
            "status": "failed_invalid_destination_stop_coordinates",
            "destination_stop_id": destination_stop_id,
        }

    def distance_km(row: dict[str, Any]) -> float | None:
        event_lat = parse_coordinate(row.get("LAT"), -90, 90)
        event_lon = parse_coordinate(row.get("LOT"), -180, 180)
        if event_lat is None or event_lon is None:
            return None
        lat_delta = math.radians(event_lat - latitude)
        lon_delta = math.radians(event_lon - longitude)
        root = (math.sin(lat_delta / 2) ** 2
                + math.cos(math.radians(latitude))
                * math.cos(math.radians(event_lat))
                * math.sin(lon_delta / 2) ** 2)
        return 6371 * 2 * math.asin(math.sqrt(root))

    filtered = []
    for row in rows:
        distance = distance_km(row)
        if distance is not None and distance <= radius_km:
            filtered.append(row)
    return filtered, {
        "status": "applied_straight_line_distance",
        "destination_stop_id": destination_stop_id,
        "radius_km": radius_km,
    }


def parse_coordinate(value: Any, minimum: float, maximum: float) -> float | None:
    try:
        coordinate = float(value)
    except (TypeError, ValueError):
        return None
    return coordinate if minimum <= coordinate <= maximum else None


def build_trip_context(
    execution: dict[str, Any],
    timetable: Any,
    trip: Any,
    destination_filter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stops = {stop.stop_id: stop for stop in timetable.stops}
    destination_filter = destination_filter or {}
    destination_relevance = (
        "verified"
        if destination_filter.get("status") == "applied_straight_line_distance"
        else "unknown"
    )
    return {
        "from_stop_id": trip.from_stop_id,
        "to_stop_id": trip.to_stop_id,
        "from_stop_name": stops[trip.from_stop_id].name_ko,
        "to_stop_name": stops[trip.to_stop_id].name_ko,
        "service_date": execution["service_date"].isoformat(),
        "departure_time": trip.departure_time,
        "requested_departure_time": execution["preferred_departure_time"].strftime("%H:%M"),
        "estimated_arrival_time": trip.estimated_arrival_time,
        "travel_minutes": trip.travel_minutes,
        "arrival_time_source": "calculated",
        "verification_status": "daily_service_weather_exception",
        "operating_days_status": timetable.operating_days_status,
        "operation_policy_confirmed": timetable.operation_policy_confirmed,
        "weather_disruption_notice": timetable.weather_disruption_notice,
        "destination_relevance": destination_relevance,
        "destination_evidence": (
            [
                "destination_filter:straight_line_distance",
                f"destination_radius_km:{destination_filter.get('radius_km')}",
            ]
            if destination_relevance == "verified"
            else []
        ),
        "evidence_ids": [f"timetable:{timetable.dataset_id}"],
        "companion_type": execution["companion_type"],
        "activity_type": execution["activity_type"],
    }


def build_itinerary(
    execution: dict[str, Any], timetable: Any, trip: Any,
    final_result: dict[str, Any], candidates: list[dict[str, Any]],
) -> ItineraryResponse:
    recommended = final_result["recommendations"][0]
    candidate_id = recommended.get("candidate_id", recommended.get("event_id"))
    candidate = next(
        item
        for item in candidates
        if item.get("candidate_id", item.get("event_id")) == candidate_id
    )
    stops = {stop.stop_id: stop for stop in timetable.stops}
    service_date = execution["service_date"]
    departure = datetime.combine(service_date, local_time.fromisoformat(trip.departure_time), SEOUL_TIMEZONE)
    arrival = datetime.combine(service_date, local_time.fromisoformat(trip.estimated_arrival_time), SEOUL_TIMEZONE)
    ferry = ItineraryItemResponse(
        sequence_no=1, item_type="FERRY", title=f"{stops[trip.from_stop_id].name_ko}에서 {stops[trip.to_stop_id].name_ko}로 이동",
        start_at=departure, end_at=arrival,
        reason="공식 시간표의 출발 시각과 구간 소요시간으로 계산한 예상 이동입니다. 한강버스는 매일 운항하지만 기상 상황에 따라 일시 중단될 수 있습니다.",
        sources=[SourceReference(type="TIMETABLE", title="한강버스 공식 시간표", url=timetable.source_url)],
        location=None, place_name=None, address=None,
        departure_stop=StopReference(stop_id=trip.from_stop_id, name=stops[trip.from_stop_id].name_ko),
        arrival_stop=StopReference(stop_id=trip.to_stop_id, name=stops[trip.to_stop_id].name_ko),
    )
    visit_start_text = candidate.get("planned_visit_start_time") or candidate.get("event_start_time")
    visit_start = (
        datetime.combine(
            service_date,
            local_time.fromisoformat(visit_start_text),
            SEOUL_TIMEZONE,
        )
        if visit_start_text and candidate.get("candidate_type", "EVENT") == "EVENT" else None
    )
    visit_end = (
        datetime.combine(service_date, local_time.fromisoformat(candidate["event_end_time"]), SEOUL_TIMEZONE)
        if candidate.get("event_end_time") and candidate.get("candidate_type", "EVENT") == "EVENT" else None
    )
    candidate_type = candidate.get("candidate_type", "EVENT")
    itinerary_item = ItineraryItemResponse(
        sequence_no=2,
        item_type=candidate_type,
        title=candidate["title"],
        start_at=visit_start,
        end_at=visit_end,
        reason=recommended["reason"],
        sources=[SourceReference(
            type="CULTURE_EVENT" if candidate_type == "EVENT" else "PLACE_CATALOG",
            title=candidate["title"],
            url=candidate.get("detail_url"),
        )],
        location=(
            LocationResponse(
                latitude=candidate["latitude"],
                longitude=candidate["longitude"],
            )
            if candidate.get("latitude") is not None
            and candidate.get("longitude") is not None
            else None
        ),
        place_name=candidate.get("place") or candidate["title"],
        address=candidate.get("address"),
        departure_stop=None,
        arrival_stop=None,
    )
    title_suffix = "전시·공연 나들이" if candidate_type == "EVENT" else "상설 장소 나들이"
    return ItineraryResponse(
        id=execution["run_id"],
        title=f"{stops[trip.from_stop_id].name_ko} 출발 {title_suffix}",
        items=[ferry, itinerary_item],
    )


def run_worker() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        compile_langgraph_for_readiness()
        start_langgraph_health_server()
    except Exception:
        LOGGER.error('LangGraph worker readiness initialization failed.')
        raise
    LOGGER.info("Itinerary worker started.")
    while True:
        try:
            if not process_next_execution():
                time.sleep(POLL_INTERVAL_SECONDS)
        except Exception:
            LOGGER.error("Itinerary worker could not process its queue. Retrying.")
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_worker()
