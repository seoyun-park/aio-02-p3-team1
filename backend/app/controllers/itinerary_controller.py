from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from psycopg import Connection

from app.core.database import get_database_connection
from app.repositories.itinerary_repository import (
    GenerationAlreadyRunning,
    ItineraryGroupNotFound,
    RegenerationLimitReached,
    VisitorSessionNotActive,
)
from app.repositories.timetable_repository import TimetableDataUnavailable
from app.schemas.itinerary import (
    ItineraryAcceptedResponse,
    ItineraryCreateRequest,
    ItineraryRunStatusResponse,
    PublicErrorResponse,
    RegenerationAcceptedResponse,
)
from app.services.timetable_service import TimetableQueryError
from app.services.itinerary_request_service import (
    create_initial_itinerary_execution,
    create_regeneration_execution,
    get_itinerary_run_status,
    parse_visitor_session_id,
    resolve_or_create_visitor_session,
    validate_itinerary_route,
)


router = APIRouter(tags=['itineraries'])


@router.post(
    '/api/itineraries',
    status_code=202,
    response_model=ItineraryAcceptedResponse,
)
def create_itinerary(
    payload: ItineraryCreateRequest,
    request: Request,
    response: Response,
    connection: Connection = Depends(get_database_connection),
) -> ItineraryAcceptedResponse | JSONResponse:
    try:
        validate_itinerary_route(payload)
    except TimetableQueryError as error:
        return api_error(error.status_code, error.error_code, error.message)
    except TimetableDataUnavailable:
        return api_error(
            503,
            'TIMETABLE_UNAVAILABLE',
            '현재 시간표를 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.',
        )

    existing_session_id = parse_visitor_session_id(
        request.session.get('visitor_session_id')
    )
    visitor_session_id, session_expires_at = resolve_or_create_visitor_session(
        connection,
        request.session.get('visitor_session_id'),
    )
    if existing_session_id != visitor_session_id:
        request.session['visitor_session_id'] = str(visitor_session_id)

    try:
        group_id, run_id = create_initial_itinerary_execution(
            connection,
            visitor_session_id,
            session_expires_at,
            payload,
        )
    except VisitorSessionNotActive:
        return api_error(
            401,
            'VISITOR_SESSION_EXPIRED',
            '방문 세션이 만료되었습니다. 다시 비밀번호를 입력해 주세요.',
        )

    response.headers['Location'] = f'/api/runs/{run_id}'
    return ItineraryAcceptedResponse(
        group_id=group_id,
        run_id=run_id,
        generation_index=0,
        status='RUNNING',
    )


@router.get(
    '/api/runs/{run_id}',
    response_model=ItineraryRunStatusResponse,
    responses={404: {'model': PublicErrorResponse}},
)
def get_itinerary_run(
    run_id: UUID,
    request: Request,
    connection: Connection = Depends(get_database_connection),
) -> ItineraryRunStatusResponse | JSONResponse:
    visitor_session_id = parse_visitor_session_id(
        request.session.get('visitor_session_id')
    )
    if visitor_session_id is None:
        return api_error(404, 'RUN_NOT_FOUND', '실행을 찾을 수 없습니다.')

    run_data = get_itinerary_run_status(connection, run_id, visitor_session_id)
    if run_data is None:
        return api_error(404, 'RUN_NOT_FOUND', '실행을 찾을 수 없습니다.')
    return ItineraryRunStatusResponse.model_validate(run_data)


@router.post(
    '/api/itineraries/{group_id}/regenerate',
    status_code=202,
    response_model=RegenerationAcceptedResponse,
    responses={
        404: {'model': PublicErrorResponse},
        409: {'model': PublicErrorResponse},
    },
)
def regenerate_itinerary(
    group_id: UUID,
    request: Request,
    response: Response,
    connection: Connection = Depends(get_database_connection),
) -> RegenerationAcceptedResponse | JSONResponse:
    visitor_session_id = parse_visitor_session_id(
        request.session.get('visitor_session_id')
    )
    if visitor_session_id is None:
        return api_error(404, 'GROUP_NOT_FOUND', '코스 그룹을 찾을 수 없습니다.')

    try:
        run_id, generation_index = create_regeneration_execution(
            connection,
            visitor_session_id,
            group_id,
        )
    except ItineraryGroupNotFound:
        return api_error(404, 'GROUP_NOT_FOUND', '코스 그룹을 찾을 수 없습니다.')
    except RegenerationLimitReached:
        return api_error(
            409,
            'REGENERATION_LIMIT_REACHED',
            '다시 추천할 수 있는 횟수를 모두 사용했습니다.',
        )
    except GenerationAlreadyRunning:
        return api_error(
            409,
            'GENERATION_ALREADY_RUNNING',
            '현재 코스를 생성 중입니다. 완료 후 다시 시도해 주세요.',
        )

    response.headers['Location'] = f'/api/runs/{run_id}'
    return RegenerationAcceptedResponse(
        group_id=group_id,
        run_id=run_id,
        generation_index=generation_index,
        status='RUNNING',
    )


def api_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={'error': {'code': code, 'message': message}},
    )
