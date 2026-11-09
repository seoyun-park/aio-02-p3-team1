from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.agents.demo_models import create_demo_agent_models
from app.agents.itinerary_graph import build_itinerary_graph
from app.itinerary_worker import (
    build_itinerary,
    build_nearby_place_candidates,
    build_candidates,
    event_matches_date,
    filter_candidates_after_arrival,
    parse_event_time_window,
)
from app.models.timetable import Stop


SERVICE_DATE = date(2026, 11, 13)
REPRODUCTION_CONTEXT = {
    "service_date": SERVICE_DATE,
    "origin_stop_id": "MANGWON",
    "destination_stop_id": "YEOUIDO",
    "departure_time": "12:42",
    "estimated_arrival_time": "13:08",
    "companion_type": "family",
    "activity_type": "exhibition_performance",
}


def test_november_13_yeouido_exhibition_open_after_arrival_is_kept():
    event = {
        "TITLE": "붙잡을 수 없는 환영을 지나",
        "CODENAME": "전시/미술",
        "STRTDATE": "2026-11-13 00:00:00.0",
        "END_DATE": "2026-11-18 00:00:00.0",
        "DATE": "2026-11-13~2026-11-18",
        "PRO_TIME": "12:00~19:00",
        "PLACE": "갤러리 지하",
        "HMPG_ADDR": "https://culture.example/events/yeouido-exhibition",
        "LAT": "37.52",
        "LOT": "126.92",
    }

    assert REPRODUCTION_CONTEXT["service_date"] == SERVICE_DATE
    assert event_matches_date(event, REPRODUCTION_CONTEXT["service_date"])
    candidates = build_candidates([event], REPRODUCTION_CONTEXT["service_date"])

    assert candidates[0]["event_start_time"] == "12:00"
    assert candidates[0]["event_end_time"] == "19:00"
    assert candidates[0]["event_time_kind"] == "opening_hours"

    eligible = filter_candidates_after_arrival(
        candidates,
        REPRODUCTION_CONTEXT["estimated_arrival_time"],
    )

    assert len(eligible) == 1
    assert eligible[0]["planned_visit_start_time"] == "13:08"


def test_event_date_filter_uses_requested_date_across_the_catalog_range():
    event = {
        "STRTDATE": "2026-12-01 00:00:00.0",
        "END_DATE": "2027-01-05 00:00:00.0",
    }

    assert event_matches_date(event, date(2026, 12, 20)) is True
    assert event_matches_date(event, date(2027, 1, 6)) is False


def test_event_interval_that_closed_before_arrival_is_excluded():
    event = {
        "event_id": "ended-exhibition",
        "event_start_time": "10:00",
        "event_end_time": "13:00",
        "event_time_kind": "opening_hours",
    }

    assert filter_candidates_after_arrival([event], "13:08") == []


def test_performance_interval_that_started_before_arrival_is_not_treated_as_opening_hours():
    event = {
        "event_id": "ongoing-performance",
        "event_start_time": "12:00",
        "event_end_time": "19:00",
        "event_time_kind": "session",
    }

    assert filter_candidates_after_arrival([event], "13:08") == []


def test_candidate_limit_applies_after_arrival_filtering():
    rows = [
        {
            "TITLE": f"시간 미확인 행사 {index}",
            "HMPG_ADDR": f"https://culture.example/events/{index}",
            "PRO_TIME": "프로그램별 상이",
        }
        for index in range(20)
    ]
    rows.append({
        "TITLE": "도착 후 시작하는 전시",
        "CODENAME": "전시/미술",
        "PRO_TIME": "14:00~18:00",
        "HMPG_ADDR": "https://culture.example/events/after-arrival",
    })

    candidates = build_candidates(rows, SERVICE_DATE)
    eligible = filter_candidates_after_arrival(candidates, "13:08")

    assert len(candidates) == 21
    assert [candidate["title"] for candidate in eligible] == ["도착 후 시작하는 전시"]
    assert eligible[0]["planned_visit_start_time"] == "14:00"


def test_single_opening_interval_is_parsed_without_inventing_a_start_time():
    assert parse_event_time_window("12:00~19:00", SERVICE_DATE) == ("12:00", "19:00")
    assert parse_event_time_window("프로그램별 상이", SERVICE_DATE) == (None, None)


def make_place(**overrides):
    fields = {
        "place_id": "park-1",
        "name": "여의도 한강공원",
        "category": "PARK",
        "road_address": "서울 영등포구 여의동로",
        "lot_address": None,
        "district": "영등포구",
        "latitude": Decimal("37.5260"),
        "longitude": Decimal("126.9320"),
        "phone": None,
        "website": None,
        "managing_org": None,
        "description": "장소 카탈로그에 등록된 공원",
        "facilities": None,
        "opening_hours": None,
        "closed_days": None,
        "admission_info": None,
        "subtype": None,
        "source_count": 1,
        "primary_source_id": "source-1",
        "source_url": "https://example.org/place/park-1",
        "last_collected_at": None,
        "last_verified_at": None,
        "is_active": True,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_no_event_fallback_selects_only_nearby_activity_compatible_places(monkeypatch):
    monkeypatch.setenv(
        "FERRY_STOP_COORDINATES_JSON",
        '{"YEOUIDO": [37.525, 126.93]}',
    )
    monkeypatch.setenv("EVENT_DESTINATION_RADIUS_KM", "5")
    monkeypatch.setattr(
        "app.itinerary_worker.list_active_recommendation_places",
        lambda _connection, categories: (
            make_place(),
            make_place(
                place_id="museum-far",
                name="먼 박물관",
                category="MUSEUM",
                latitude=Decimal("37.70"),
                longitude=Decimal("127.10"),
            ),
        ) if "PARK" in categories else (),
    )

    candidates, metadata = build_nearby_place_candidates(
        None,
        "YEOUIDO",
        "walk_healing",
    )

    assert len(candidates) == 1
    assert candidates[0]["candidate_id"] == "place:park-1"
    assert candidates[0]["candidate_type"] == "PLACE"
    assert candidates[0]["destination_distance_km"] < 5
    assert metadata["distance_semantics"] == "straight_line_not_route_distance"


def test_food_preference_does_not_get_mislabeled_as_a_catalog_place():
    candidates, metadata = build_nearby_place_candidates(
        None,
        "YEOUIDO",
        "food_cafe",
    )

    assert candidates == []
    assert metadata["status"] == "no_catalog_categories_for_activity"


def test_graph_can_select_a_place_without_claiming_event_dates_or_opening_hours():
    trip_context = {
        "verification_status": "daily_service_weather_exception",
        "operation_policy_confirmed": True,
        "from_stop_id": "MANGWON",
        "to_stop_id": "YEOUIDO",
        "departure_time": "12:42",
        "requested_departure_time": "12:42",
        "estimated_arrival_time": "13:08",
        "arrival_time_source": "calculated",
        "service_date": "2026-11-13",
        "destination_relevance": "verified",
        "destination_evidence": ["destination_filter:straight_line_distance"],
        "evidence_ids": ["timetable:test", "destination_filter:straight_line_distance"],
    }
    candidate = {
        "candidate_id": "place:park-1",
        "candidate_type": "PLACE",
        "title": "여의도 한강공원",
        "category": "PARK",
        "description": "장소 카탈로그에 등록된 공원",
        "opening_hours": None,
        "closed_days": None,
        "event_is_active_on_service_date": None,
        "candidate_is_valid_for_service_date": True,
        "review_status": "verified",
        "eligibility_status": "POSSIBLE",
        "evidence_ids": ["place-catalog:park-1"],
        "destination_distance_km": 0.2,
    }

    result = build_itinerary_graph(create_demo_agent_models()).invoke({
        "run_id": "place-fallback-test",
        "trip_context": trip_context,
        "candidates": [candidate],
        "revision_count": 0,
    })

    assert result["final_result"]["result_type"] == "eligible_candidates"
    recommendation = result["final_result"]["recommendations"][0]
    assert recommendation["candidate_type"] == "PLACE"
    assert recommendation["candidate_id"] == "place:park-1"
    assert "운영시간과 휴관 여부" in recommendation["reason"]


def test_place_itinerary_item_uses_catalog_source_without_inventing_visit_time():
    timetable = SimpleNamespace(
        stops=(
            Stop("MANGWON", "망원", 1),
            Stop("YEOUIDO", "여의도", 2),
        ),
        source_url="https://example.org/timetable",
    )
    trip = SimpleNamespace(
        from_stop_id="MANGWON",
        to_stop_id="YEOUIDO",
        departure_time="12:42",
        estimated_arrival_time="13:08",
    )
    execution = {"run_id": "00000000-0000-0000-0000-000000000001", "service_date": SERVICE_DATE}
    place_candidate = {
        "candidate_id": "place:park-1",
        "candidate_type": "PLACE",
        "title": "여의도 한강공원",
        "place": "여의도 한강공원",
        "address": "서울 영등포구 여의동로",
        "latitude": 37.526,
        "longitude": 126.932,
        "detail_url": "https://example.org/place/park-1",
    }
    final_result = {
        "recommendations": [{
            "candidate_id": "place:park-1",
            "candidate_type": "PLACE",
            "reason": "도착지 인근 장소 카탈로그 후보입니다.",
        }],
    }

    itinerary = build_itinerary(
        execution,
        timetable,
        trip,
        final_result,
        [place_candidate],
    )

    place_item = itinerary.items[1]
    assert place_item.item_type == "PLACE"
    assert place_item.start_at is None
    assert place_item.end_at is None
    assert place_item.sources[0].type == "PLACE_CATALOG"
    assert place_item.location.latitude == 37.526
