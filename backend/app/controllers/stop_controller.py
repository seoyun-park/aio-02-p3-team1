from fastapi import APIRouter, HTTPException

from app.repositories.timetable_repository import TimetableDataUnavailable
from app.schemas.timetable import StopListResponse
from app.services.timetable_service import list_stops as list_timetable_stops
from app.views.timetable_view import render_stop_list_response

router = APIRouter(tags=['stops'])


@router.get('/api/v1/ferry/stops', response_model=StopListResponse)
@router.get('/api/stops', response_model=StopListResponse, include_in_schema=False)
def list_stops() -> StopListResponse:
    try:
        snapshot, stops = list_timetable_stops()
    except TimetableDataUnavailable as error:
        raise HTTPException(
            status_code=503,
            detail={'code': 'timetable_unavailable', 'message': str(error)},
        ) from error
    return render_stop_list_response(snapshot, stops)
