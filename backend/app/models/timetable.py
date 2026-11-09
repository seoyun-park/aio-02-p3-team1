from dataclasses import dataclass


@dataclass(frozen=True)
class Stop:
    stop_id: str
    name_ko: str
    display_order: int


@dataclass(frozen=True)
class TripEstimate:
    from_stop_id: str
    to_stop_id: str
    departure_time: str
    travel_minutes: int
    estimated_arrival_time: str
    arrival_day_offset: int
    arrival_time_source: str
    stop_call_status: str


@dataclass(frozen=True)
class TimetableSnapshot:
    dataset_id: str
    timezone: str
    source_url: str
    effective_from: str
    effective_until: str | None
    operating_days_status: str
    separate_operational_notices_reviewed: bool
    operation_policy_confirmed: bool
    weather_disruption_notice: str
    stops: tuple[Stop, ...]
    trip_estimates: tuple[TripEstimate, ...]
