from fastapi import APIRouter, Depends, HTTPException, Path as PathParameter, Query
from psycopg import Connection

from app.core.database import get_database_connection
from app.schemas.place import PlaceCategory, PlaceItemResponse, PlaceListResponse
from app.services.place_service import PlaceNotFound, get_place, list_places
from app.views.place_view import render_place, render_place_list


router = APIRouter(tags=['places'])


@router.get('/api/v1/places', response_model=PlaceListResponse)
def get_places(
    category: PlaceCategory | None = Query(default=None),
    district: str | None = Query(default=None, min_length=1, max_length=32),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    connection: Connection = Depends(get_database_connection),
) -> PlaceListResponse:
    collection = list_places(
        connection,
        category,
        district.strip() if district else None,
        limit,
        offset,
    )
    return render_place_list(collection)


@router.get('/api/v1/places/{place_id}', response_model=PlaceItemResponse)
def get_place_by_id(
    place_id: str = PathParameter(min_length=1, max_length=64),
    connection: Connection = Depends(get_database_connection),
) -> PlaceItemResponse:
    try:
        place = get_place(connection, place_id)
    except PlaceNotFound as error:
        raise HTTPException(
            status_code=404,
            detail={'code': 'place_not_found', 'message': str(error)},
        ) from error
    return render_place(place)
