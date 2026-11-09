from psycopg import Connection

from app.models.place import Place, PlaceCollection
from app.repositories.place_repository import (
    count_active_places,
    get_active_place,
    list_active_places,
)


class PlaceNotFound(LookupError):
    """Raised when an active place cannot be found."""


def list_places(
    connection: Connection,
    category: str | None,
    district: str | None,
    limit: int,
    offset: int,
) -> PlaceCollection:
    return PlaceCollection(
        total_count=count_active_places(connection, category, district),
        limit=limit,
        offset=offset,
        places=list_active_places(connection, category, district, limit, offset),
    )


def get_place(connection: Connection, place_id: str) -> Place:
    place = get_active_place(connection, place_id)
    if place is None:
        raise PlaceNotFound('장소를 찾을 수 없습니다.')
    return place
