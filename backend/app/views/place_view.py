from app.models.place import Place, PlaceCollection
from app.schemas.place import PlaceItemResponse, PlaceListResponse


def render_place(place: Place) -> PlaceItemResponse:
    return PlaceItemResponse(
        place_id=place.place_id,
        name=place.name,
        category=place.category,
        road_address=place.road_address,
        lot_address=place.lot_address,
        district=place.district,
        latitude=float(place.latitude) if place.latitude is not None else None,
        longitude=float(place.longitude) if place.longitude is not None else None,
        phone=place.phone,
        website=place.website,
        managing_org=place.managing_org,
        description=place.description,
        facilities=place.facilities,
        opening_hours=place.opening_hours,
        closed_days=place.closed_days,
        admission_info=place.admission_info,
        subtype=place.subtype,
        source_count=place.source_count,
        primary_source_id=place.primary_source_id,
        source_url=place.source_url,
        last_collected_at=place.last_collected_at,
        last_verified_at=place.last_verified_at,
        is_active=place.is_active,
    )


def render_place_list(collection: PlaceCollection) -> PlaceListResponse:
    return PlaceListResponse(
        total_count=collection.total_count,
        limit=collection.limit,
        offset=collection.offset,
        places=[render_place(place) for place in collection.places],
    )
