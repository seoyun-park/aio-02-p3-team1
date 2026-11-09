from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.models.timetable import Stop, TimetableSnapshot, TripEstimate
from app.repositories.timetable_repository import get_timetable_snapshot


class TimetableQueryError(Exception):
    def __init__(self, status_code: int, error_code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message


def list_stops() -> tuple[TimetableSnapshot, tuple[Stop, ...]]:
    snapshot = get_timetable_snapshot()
    return snapshot, tuple(sorted(snapshot.stops, key=lambda stop: stop.display_order))


def list_trip_estimates_by_route(
    from_stop_id: str,
    to_stop_id: str,
) -> tuple[TimetableSnapshot, tuple[TripEstimate, ...]]:
    snapshot = get_timetable_snapshot()
    normalized_from_stop_id = from_stop_id.strip().upper()
    normalized_to_stop_id = to_stop_id.strip().upper()
    stop_ids = {stop.stop_id for stop in snapshot.stops}

    if normalized_from_stop_id not in stop_ids:
        raise TimetableQueryError(404, 'stop_not_found', '출발 선착장을 찾을 수 없습니다.')
    if normalized_to_stop_id not in stop_ids:
        raise TimetableQueryError(404, 'stop_not_found', '도착 선착장을 찾을 수 없습니다.')
    if normalized_from_stop_id == normalized_to_stop_id:
        raise TimetableQueryError(422, 'same_stop', '출발지와 도착지는 서로 달라야 합니다.')

    estimates = tuple(
        sorted(
            (
                estimate
                for estimate in snapshot.trip_estimates
                if estimate.from_stop_id == normalized_from_stop_id
                and estimate.to_stop_id == normalized_to_stop_id
            ),
            key=lambda estimate: estimate.departure_time,
        )
    )
    if not estimates:
        raise TimetableQueryError(
            404,
            'route_not_in_timetable',
            '해당 출발·도착 조합은 현재 시간표 데이터에 없습니다.',
        )

    return snapshot, estimates


def list_departures_for_date(
    from_stop_id: str,
    to_stop_id: str,
    service_date: date,
) -> tuple[TimetableSnapshot, tuple[TripEstimate, ...]]:
    snapshot, estimates = list_trip_estimates_by_route(from_stop_id, to_stop_id)
    seoul_timezone = ZoneInfo(snapshot.timezone)
    now_in_seoul = datetime.now(seoul_timezone)
    if service_date < now_in_seoul.date():
        raise TimetableQueryError(
            422,
            'service_date_in_past',
            '지난 날짜의 배편은 조회할 수 없습니다.',
        )
    if service_date > now_in_seoul.date():
        return snapshot, estimates

    remaining_estimates = tuple(
        estimate
        for estimate in estimates
        if time.fromisoformat(estimate.departure_time) > now_in_seoul.time().replace(
            tzinfo=None, second=0, microsecond=0
        )
    )
    return snapshot, remaining_estimates


def get_departure_estimate(
    from_stop_id: str,
    to_stop_id: str,
    service_date: date,
    departure_time: time,
) -> tuple[TimetableSnapshot, TripEstimate]:
    snapshot, estimates = list_departures_for_date(
        from_stop_id,
        to_stop_id,
        service_date,
    )
    requested_departure = departure_time.strftime('%H:%M')
    matching_estimate = next(
        (
            estimate
            for estimate in estimates
            if estimate.departure_time == requested_departure
        ),
        None,
    )
    if matching_estimate is None:
        raise TimetableQueryError(
            422,
            'departure_not_available',
            '선택한 날짜와 출발 시각에 이용할 수 있는 시간표 편이 없습니다.',
        )
    return snapshot, matching_estimate
