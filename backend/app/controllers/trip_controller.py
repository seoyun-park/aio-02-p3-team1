from datetime import date, datetime
from fastapi import APIRouter, HTTPException, Query
from zoneinfo import ZoneInfo

from app.repositories.timetable_repository import TimetableDataUnavailable
from app.schemas.timetable import TripQueryResponse
from app.services.timetable_service import (
    TimetableQueryError,
    list_departures_for_date,
)
from app.views.timetable_view import render_trip_query_response

router = APIRouter(tags=['trips'])


@router.get('/api/v1/ferry/trips', response_model=TripQueryResponse)
@router.get('/api/trips', response_model=TripQueryResponse, include_in_schema=False)
def list_trips(
    from_stop_id: str = Query(min_length=1, max_length=32),
    to_stop_id: str = Query(min_length=1, max_length=32),
    service_date: date | None = Query(default=None),
) -> TripQueryResponse:
    try:
        selected_date = service_date or datetime.now(ZoneInfo('Asia/Seoul')).date()
        snapshot, estimates = list_departures_for_date(
            from_stop_id,
            to_stop_id,
            selected_date,
        )
    except TimetableQueryError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={'code': error.error_code, 'message': error.message},
        ) from error
    except TimetableDataUnavailable as error:
        raise HTTPException(
            status_code=503,
            detail={'code': 'timetable_unavailable', 'message': str(error)},
        ) from error
    response = render_trip_query_response(snapshot, estimates)
    response.metadata.service_date = selected_date
    response.metadata.schedule_reuse_assumption = 'daily_service_with_weather_exception'
    return response
