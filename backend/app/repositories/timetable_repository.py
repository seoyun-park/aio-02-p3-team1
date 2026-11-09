import json
from functools import lru_cache
from json import JSONDecodeError
from pathlib import Path

from app.models.timetable import Stop, TimetableSnapshot, TripEstimate


class TimetableDataUnavailable(RuntimeError):
    """Raised when the checked-in timetable cannot be read or parsed."""


@lru_cache(maxsize=1)
def get_timetable_snapshot() -> TimetableSnapshot:
    data_path = (
        Path(__file__).resolve().parents[2]
        / 'data'
        / 'processed'
        / 'timetable.json'
    )

    try:
        timetable_data = json.loads(data_path.read_text(encoding='utf-8'))
        stops = tuple(
            Stop(
                stop_id=stop_data['stop_id'],
                name_ko=stop_data['name_ko'],
                display_order=int(stop_data['display_order']),
            )
            for stop_data in timetable_data['stops']
        )
        trip_estimates = tuple(
            TripEstimate(
                from_stop_id=departure_group['from_stop_id'],
                to_stop_id=destination_data['to_stop_id'],
                departure_time=departure_group['departure_time'],
                travel_minutes=int(destination_data['travel_minutes']),
                estimated_arrival_time=destination_data['estimated_arrival_time'],
                arrival_day_offset=int(destination_data['arrival_day_offset']),
                arrival_time_source=map_arrival_time_source(
                    destination_data['arrival_time_source']
                ),
                stop_call_status=destination_data['stop_call_status'],
            )
            for departure_group in timetable_data['arrival_estimates']
            for destination_data in departure_group['destinations']
        )
        validity = timetable_data['validity']
        source_data = timetable_data['source']

        if not stops or not trip_estimates:
            raise ValueError('timetable contains no stops or estimates')

        return TimetableSnapshot(
            dataset_id=timetable_data['dataset_id'],
            timezone=timetable_data['timezone'],
            source_url=source_data['page_url'],
            effective_from=validity['effective_from'],
            effective_until=validity['effective_until'],
            operating_days_status=validity['operating_days_status'],
            separate_operational_notices_reviewed=validity[
                'separate_operational_notices_reviewed'
            ],
            operation_policy_confirmed=validity.get(
                'operation_policy_confirmed', False
            ),
            weather_disruption_notice=validity.get(
                'weather_disruption_notice',
                '강풍·비바람 등 기상 상황에 따라 안전 운항을 위해 일시적으로 운항이 중단될 수 있습니다.',
            ),
            stops=stops,
            trip_estimates=trip_estimates,
        )
    except (OSError, JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise TimetableDataUnavailable(
            f'Checked timetable data could not be loaded from {data_path.name}.'
        ) from error


def map_arrival_time_source(arrival_time_source: str) -> str:
    if arrival_time_source == 'calculated_from_origin_departure_and_diagram_durations':
        return 'calculated'
    raise ValueError(f'Unsupported arrival time source: {arrival_time_source}')
