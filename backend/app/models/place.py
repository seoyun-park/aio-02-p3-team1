from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Place:
    place_id: str
    name: str
    category: str
    road_address: str | None
    lot_address: str | None
    district: str | None
    latitude: Decimal | None
    longitude: Decimal | None
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


@dataclass(frozen=True)
class PlaceCollection:
    total_count: int
    limit: int
    offset: int
    places: tuple[Place, ...]
