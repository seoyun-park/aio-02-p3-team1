from datetime import datetime
from typing import Literal

from pydantic import BaseModel


PlaceCategory = Literal[
    'MUSEUM', 'ART_GALLERY', 'BOTANICAL_GARDEN', 'PARK',
    'EXHIBITION_HALL', 'CULTURAL_SPACE'
]


class PlaceItemResponse(BaseModel):
    place_id: str
    name: str
    category: PlaceCategory
    road_address: str | None
    lot_address: str | None
    district: str | None
    latitude: float | None
    longitude: float | None
    phone: str | None
    website: str | None
    managing_org: str | None
    description: str | None
    facilities: str | None
    opening_hours: str | None
    closed_days: str | None
    admission_info: str | None
    subtype: str | None
    source_count: int
    primary_source_id: str
    source_url: str | None
    last_collected_at: datetime
    last_verified_at: datetime | None
    is_active: bool


class PlaceListResponse(BaseModel):
    total_count: int
    limit: int
    offset: int
    places: list[PlaceItemResponse]
