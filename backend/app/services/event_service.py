import os
from datetime import datetime, timezone
from typing import Any

from app.integrations.seoul_culture_api import (
    SeoulCultureApiError,
    fetch_event_page,
)
from app.models.event import Event, EventCollection


class EventDataUnavailable(RuntimeError):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code


def list_events(limit: int, offset: int) -> EventCollection:
    api_key = os.environ.get('SEOUL_CULTURE_API_KEY', '').strip()
    if not api_key:
        raise EventDataUnavailable(
            'culture_api_not_configured',
            '서울시 문화행사 API 키가 설정되지 않았습니다.',
        )

    start_index = offset + 1
    end_index = offset + limit
    try:
        total_count, source_rows = fetch_event_page(
            api_key=api_key,
            start_index=start_index,
            end_index=end_index,
        )
    except SeoulCultureApiError as error:
        raise EventDataUnavailable(error.error_code, str(error)) from error

    return EventCollection(
        source_service='aiViewCulture',
        retrieved_at=datetime.now(timezone.utc),
        total_count=total_count,
        limit=limit,
        offset=offset,
        events=tuple(map_source_row(source_row) for source_row in source_rows),
    )


def map_source_row(source_row: dict[str, Any]) -> Event:
    fee_text = normalize_text(source_row.get('USE_FEE'))
    free_value = normalize_text(source_row.get('IS_FREE'))
    if free_value == '무료':
        free_status = 'free'
    elif free_value == '유료' and fee_text is not None:
        free_status = 'paid_with_details'
    elif free_value == '유료':
        free_status = 'paid_details_missing'
    else:
        free_status = 'unknown'

    return Event(
        title=normalize_text(source_row.get('TITLE')),
        category=normalize_text(source_row.get('CODENAME')),
        district_name=normalize_text(source_row.get('GUNAME')),
        district_code=normalize_text(source_row.get('CGG_CD')),
        start_date_text=normalize_text(source_row.get('STRTDATE')),
        end_date_text=normalize_text(source_row.get('END_DATE')),
        event_date_text=normalize_text(source_row.get('DATE')),
        place=normalize_text(source_row.get('PLACE')),
        organizer_name=normalize_text(source_row.get('ORG_NAME')),
        target_audience=normalize_text(source_row.get('USE_TRGT')),
        fee_text=fee_text,
        free_status=free_status,
        inquiry=normalize_text(source_row.get('INQUIRY')),
        player=normalize_text(source_row.get('PLAYER')),
        program_text=normalize_text(source_row.get('PROGRAM')),
        description_text=normalize_text(source_row.get('ETC_DESC')),
        organization_url=normalize_text(source_row.get('ORG_LINK')),
        main_image_url=normalize_text(source_row.get('MAIN_IMG')),
        registration_date_text=normalize_text(source_row.get('RGSTDATE')),
        ticket_text=normalize_text(source_row.get('TICKET')),
        theme_code=normalize_text(source_row.get('THEMECODE')),
        program_time_text=normalize_text(source_row.get('PRO_TIME')),
        latitude=parse_coordinate(source_row.get('LAT'), -90, 90),
        longitude=parse_coordinate(source_row.get('LOT'), -180, 180),
        culture_detail_url=normalize_text(source_row.get('HMPG_ADDR')),
    )


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized_value = str(value).strip()
    return normalized_value or None


def parse_coordinate(value: Any, minimum: int, maximum: int) -> float | None:
    normalized_value = normalize_text(value)
    if normalized_value is None:
        return None
    try:
        coordinate = float(normalized_value)
    except ValueError:
        return None
    if not minimum <= coordinate <= maximum:
        return None
    return coordinate
