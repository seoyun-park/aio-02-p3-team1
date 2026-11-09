from fastapi import APIRouter, HTTPException, Query

from app.schemas.event import EventListResponse
from app.services.event_service import EventDataUnavailable, list_events as list_live_events
from app.views.event_view import render_event_list_response


router = APIRouter(tags=['events'])


@router.get('/api/v1/events', response_model=EventListResponse)
@router.get('/api/events', response_model=EventListResponse, include_in_schema=False)
def list_events(
    limit: int = Query(default=20, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> EventListResponse:
    try:
        collection = list_live_events(
            limit=limit,
            offset=offset,
        )
    except EventDataUnavailable as error:
        raise HTTPException(
            status_code=503,
            detail={'code': error.error_code, 'message': str(error)},
        ) from error
    return render_event_list_response(collection)
