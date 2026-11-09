from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class EventItemResponse(BaseModel):
    title: str | None
    category: str | None
    district_name: str | None
    district_code: str | None
    start_date_text: str | None
    end_date_text: str | None
    event_date_text: str | None
    place: str | None
    organizer_name: str | None
    target_audience: str | None
    free_status: Literal[
        'free', 'paid_with_details', 'paid_details_missing', 'unknown'
    ]
    fee_text: str | None
    inquiry: str | None
    player: str | None
    program_text: str | None
    description_text: str | None
    organization_url: str | None
    main_image_url: str | None
    registration_date_text: str | None
    ticket_text: str | None
    theme_code: str | None
    program_time_text: str | None
    latitude: float | None
    longitude: float | None
    culture_detail_url: str | None


class EventListResponse(BaseModel):
    source_service: Literal['aiViewCulture']
    retrieved_at: datetime
    total_count: int
    limit: int
    offset: int
    events: list[EventItemResponse]
