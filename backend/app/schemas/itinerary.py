from datetime import date, datetime, time
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


CompanionType = Literal['family', 'couple', 'friends', 'solo']
ActivityType = Literal[
    'exhibition_performance',
    'culture_event',
    'walk_healing',
    'food_cafe',
]
ItineraryItemType = Literal['FERRY', 'EVENT', 'PLACE', 'WALK']
SourceType = Literal['TIMETABLE', 'CULTURE_EVENT', 'PLACE_CATALOG']
PublicRunStatus = Literal['RUNNING', 'SUCCESS', 'NO_RESULT', 'FAILED']


class ItineraryCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    origin_stop_id: str = Field(min_length=1, max_length=32)
    destination_stop_id: str = Field(min_length=1, max_length=32)
    service_date: date
    preferred_departure_time: time
    companion_type: CompanionType
    activity_type: ActivityType

    @field_validator('origin_stop_id', 'destination_stop_id')
    @classmethod
    def validate_stop_id(cls, value: str) -> str:
        normalized_value = value.strip().upper()
        if not normalized_value:
            raise ValueError('Stop ID cannot be blank.')
        return normalized_value

    @field_validator('preferred_departure_time')
    @classmethod
    def validate_minute_precision(cls, value: time) -> time:
        if value.tzinfo is not None:
            raise ValueError('preferred_departure_time is a local wall-clock time.')
        if value.second or value.microsecond:
            raise ValueError('preferred_departure_time must use HH:MM precision.')
        return value

    @field_validator('service_date')
    @classmethod
    def validate_service_date(cls, value: date) -> date:
        if value < datetime.now(ZoneInfo('Asia/Seoul')).date():
            raise ValueError('service_date cannot be in the past.')
        return value

    @model_validator(mode='after')
    def validate_distinct_stops(self) -> 'ItineraryCreateRequest':
        if self.origin_stop_id == self.destination_stop_id:
            raise ValueError('Origin and destination stops must differ.')
        return self


class ItineraryAcceptedResponse(BaseModel):
    group_id: UUID
    run_id: UUID
    generation_index: Literal[0]
    status: Literal['RUNNING']


class RegenerationAcceptedResponse(BaseModel):
    group_id: UUID
    run_id: UUID
    generation_index: Literal[1, 2]
    status: Literal['RUNNING']


class SourceReference(BaseModel):
    type: SourceType
    title: str = Field(min_length=1, max_length=300)
    url: str | None


class LocationResponse(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class StopReference(BaseModel):
    stop_id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=100)


class ItineraryItemResponse(BaseModel):
    sequence_no: int = Field(gt=0)
    item_type: ItineraryItemType
    title: str = Field(min_length=1, max_length=500)
    start_at: AwareDatetime | None
    end_at: AwareDatetime | None
    reason: str = Field(min_length=1, max_length=2000)
    sources: list[SourceReference]
    location: LocationResponse | None
    place_name: str | None = Field(max_length=300)
    address: str | None = Field(max_length=1000)
    departure_stop: StopReference | None
    arrival_stop: StopReference | None

    @model_validator(mode='after')
    def validate_item_times_and_ferry_stops(self) -> 'ItineraryItemResponse':
        if self.start_at is not None and self.end_at is not None:
            if self.end_at < self.start_at:
                raise ValueError('end_at must not precede start_at.')
        if self.item_type == 'FERRY':
            if self.departure_stop is None or self.arrival_stop is None:
                raise ValueError('FERRY items require departure and arrival stops.')
        elif self.departure_stop is not None or self.arrival_stop is not None:
            raise ValueError('Only FERRY items may contain ferry stop references.')
        return self


class ItineraryResponse(BaseModel):
    id: UUID
    title: str = Field(min_length=1, max_length=500)
    items: list[ItineraryItemResponse] = Field(min_length=1)


class NoResultReason(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)


class PublicRunError(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)
    retryable: bool


class ItineraryRunStatusResponse(BaseModel):
    group_id: UUID
    run_id: UUID
    generation_index: int = Field(ge=0, le=2)
    status: PublicRunStatus
    itinerary: ItineraryResponse | None = None
    reason: NoResultReason | None = None
    error: PublicRunError | None = None

    @model_validator(mode='after')
    def validate_status_payload(self) -> 'ItineraryRunStatusResponse':
        if self.status == 'SUCCESS' and self.itinerary is None:
            raise ValueError('SUCCESS requires an itinerary.')
        if self.status == 'NO_RESULT' and (self.itinerary is not None or self.reason is None):
            raise ValueError('NO_RESULT requires a reason and no itinerary.')
        if self.status == 'FAILED' and self.error is None:
            raise ValueError('FAILED requires an error.')
        if self.status == 'RUNNING' and any(
            value is not None for value in (self.itinerary, self.reason, self.error)
        ):
            raise ValueError('RUNNING must not include a terminal result.')
        return self


class PublicErrorResponse(BaseModel):
    error: NoResultReason
