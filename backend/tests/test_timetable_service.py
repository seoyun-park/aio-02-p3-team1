from datetime import date, datetime

import pytest

from app.models.timetable import Stop, TimetableSnapshot, TripEstimate
from app.repositories import timetable_repository
from app.repositories.timetable_repository import TimetableDataUnavailable
from app.services import timetable_service
from app.services.timetable_service import TimetableQueryError


def make_snapshot():
    stops = (
        Stop("YEOUIDO", "여의도", 2),
        Stop("MANGWON", "망원", 1),
        Stop("JAMSIL", "잠실", 3),
    )
    trips = (
        TripEstimate("MANGWON", "YEOUIDO", "14:10", 28, "14:38", 0, "calculated", "called"),
        TripEstimate("MANGWON", "YEOUIDO", "14:00", 28, "14:28", 0, "calculated", "called"),
        TripEstimate("YEOUIDO", "MANGWON", "14:05", 28, "14:33", 0, "calculated", "called"),
    )
    return TimetableSnapshot(
        dataset_id="test",
        timezone="Asia/Seoul",
        source_url="https://example.org/timetable",
        effective_from="2026-01-01",
        effective_until=None,
        operating_days_status="daily",
        separate_operational_notices_reviewed=True,
        operation_policy_confirmed=True,
        weather_disruption_notice="기상 상황에 따라 운항이 중단될 수 있습니다.",
        stops=stops,
        trip_estimates=trips,
    )


def test_list_stops_sorts_by_display_order(monkeypatch):
    snapshot = make_snapshot()
    monkeypatch.setattr(timetable_service, "get_timetable_snapshot", lambda: snapshot)

    returned_snapshot, stops = timetable_service.list_stops()

    assert returned_snapshot is snapshot
    assert [stop.stop_id for stop in stops] == ["MANGWON", "YEOUIDO", "JAMSIL"]


def test_route_estimates_are_normalized_and_sorted(monkeypatch):
    snapshot = make_snapshot()
    monkeypatch.setattr(timetable_service, "get_timetable_snapshot", lambda: snapshot)

    _, estimates = timetable_service.list_trip_estimates_by_route(" mangwon ", "yeouido")

    assert [estimate.departure_time for estimate in estimates] == ["14:00", "14:10"]


@pytest.mark.parametrize(
    ("origin", "destination", "error_code"),
    [
        ("UNKNOWN", "YEOUIDO", "stop_not_found"),
        ("MANGWON", "UNKNOWN", "stop_not_found"),
        ("MANGWON", "MANGWON", "same_stop"),
        ("JAMSIL", "MANGWON", "route_not_in_timetable"),
    ],
)
def test_invalid_route_combinations_have_typed_errors(monkeypatch, origin, destination, error_code):
    monkeypatch.setattr(timetable_service, "get_timetable_snapshot", make_snapshot)

    with pytest.raises(TimetableQueryError) as error:
        timetable_service.list_trip_estimates_by_route(origin, destination)

    assert error.value.error_code == error_code


def test_future_date_reuses_schedule_estimates(monkeypatch):
    monkeypatch.setattr(timetable_service, "get_timetable_snapshot", make_snapshot)

    _, estimates = timetable_service.list_departures_for_date(
        "MANGWON", "YEOUIDO", date(2999, 1, 1)
    )

    assert len(estimates) == 2


def test_past_date_is_rejected(monkeypatch):
    monkeypatch.setattr(timetable_service, "get_timetable_snapshot", make_snapshot)

    with pytest.raises(TimetableQueryError) as error:
        timetable_service.list_departures_for_date("MANGWON", "YEOUIDO", date(2000, 1, 1))

    assert error.value.error_code == "service_date_in_past"


def test_today_only_returns_departures_after_current_minute(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 6, 14, 0, tzinfo=tz)

    snapshot = make_snapshot()
    monkeypatch.setattr(timetable_service, "get_timetable_snapshot", lambda: snapshot)
    monkeypatch.setattr(timetable_service, "datetime", FrozenDateTime)

    _, estimates = timetable_service.list_departures_for_date(
        "MANGWON", "YEOUIDO", date(2026, 11, 6)
    )

    assert [estimate.departure_time for estimate in estimates] == ["14:10"]


def test_get_departure_estimate_requires_an_available_time(monkeypatch):
    monkeypatch.setattr(timetable_service, "get_timetable_snapshot", make_snapshot)

    _, estimate = timetable_service.get_departure_estimate(
        "MANGWON", "YEOUIDO", date(2999, 1, 1), datetime.strptime("14:10", "%H:%M").time()
    )
    assert estimate.departure_time == "14:10"

    with pytest.raises(TimetableQueryError) as error:
        timetable_service.get_departure_estimate(
            "MANGWON", "YEOUIDO", date(2999, 1, 1), datetime.strptime("15:00", "%H:%M").time()
        )
    assert error.value.error_code == "departure_not_available"


def test_checked_in_timetable_loads_and_maps_arrival_source():
    timetable_repository.get_timetable_snapshot.cache_clear()

    snapshot = timetable_repository.get_timetable_snapshot()

    assert snapshot.stops
    assert timetable_repository.map_arrival_time_source(
        "calculated_from_origin_departure_and_diagram_durations"
    ) == "calculated"


def test_invalid_timetable_source_is_reported_as_unavailable(monkeypatch):
    timetable_repository.get_timetable_snapshot.cache_clear()

    def missing_file(*_args, **_kwargs):
        raise FileNotFoundError("test-only missing source")

    monkeypatch.setattr("pathlib.Path.read_text", missing_file)
    try:
        with pytest.raises(TimetableDataUnavailable):
            timetable_repository.get_timetable_snapshot()
    finally:
        timetable_repository.get_timetable_snapshot.cache_clear()


def test_unsupported_arrival_source_is_rejected():
    with pytest.raises(ValueError, match="Unsupported arrival time source"):
        timetable_repository.map_arrival_time_source("unknown")
