from datetime import date
from typing import Literal

from pydantic import BaseModel


class StopItem(BaseModel):
    stop_id: str
    name_ko: str
    display_order: int


class TimetableMetadata(BaseModel):
    dataset_id: str
    timezone: str
    source_url: str
    effective_from: str
    effective_until: str | None
    operating_days_status: str
    separate_operational_notices_reviewed: bool
    operation_policy_confirmed: bool
    weather_disruption_notice: str
    service_date_status: Literal['unknown']
    service_date: date | None = None
    schedule_reuse_assumption: Literal[
        'daily_schedule_assumed',
        'daily_service_with_weather_exception',
    ] | None = None


class StopListResponse(BaseModel):
    metadata: TimetableMetadata
    stops: list[StopItem]


class TripEstimateResponse(BaseModel):
    from_stop_id: str
    to_stop_id: str
    departure_time: str
    travel_minutes: int
    estimated_arrival_time: str
    arrival_time_source: Literal['calculated']
    arrival_day_offset: int
    stop_call_status: str


class TripQueryResponse(BaseModel):
    metadata: TimetableMetadata
    departures: list[TripEstimateResponse]
