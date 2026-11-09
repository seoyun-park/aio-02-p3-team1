import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


API_BASE_URL = 'http://openapi.seoul.go.kr:8088'
SERVICE_NAME = 'aiViewCulture'
REQUEST_TIMEOUT_SECONDS = 8


class SeoulCultureApiError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        message: str,
        retryable: bool = False,
        http_status: int | None = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable
        self.http_status = http_status


def fetch_event_page(
    api_key: str,
    start_index: int,
    end_index: int,
) -> tuple[int, tuple[dict[str, object], ...]]:
    encoded_api_key = quote(api_key, safe='')
    request_url = (
        f'{API_BASE_URL}/{encoded_api_key}/json/{SERVICE_NAME}/'
        f'{start_index}/{end_index}'
    )
    request = Request(
        request_url,
        headers={
            'Accept': 'application/json',
            'User-Agent': 'seoul-wave-backend/1.0',
        },
    )

    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            response_body = response.read().decode('utf-8-sig')
    except HTTPError as error:
        raise SeoulCultureApiError(
            'culture_api_http_error',
            f'Seoul culture API returned HTTP {error.code}.',
            retryable=(error.code in {408, 425, 429} or error.code >= 500),
            http_status=error.code,
        ) from error
    except (URLError, TimeoutError, OSError, UnicodeError) as error:
        raise SeoulCultureApiError(
            'culture_api_unreachable',
            'Seoul culture API could not be reached.',
            retryable=True,
        ) from error

    try:
        payload = json.loads(response_body)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise SeoulCultureApiError(
            'culture_api_invalid_response',
            'Seoul culture API returned invalid JSON.',
        ) from error

    service_payload = payload.get(SERVICE_NAME) if isinstance(payload, dict) else None
    if not isinstance(service_payload, dict):
        raise SeoulCultureApiError(
            'culture_api_invalid_response',
            'Seoul culture API response did not contain the expected service.',
        )

    result_payload = service_payload.get('RESULT')
    result_code = (
        result_payload.get('CODE')
        if isinstance(result_payload, dict)
        else None
    )
    if result_code == 'INFO-200':
        return 0, ()
    if result_code != 'INFO-000':
        raise SeoulCultureApiError(
            'culture_api_service_error',
            'Seoul culture API reported an unsuccessful response.',
        )

    try:
        total_count = int(service_payload['list_total_count'])
    except (KeyError, TypeError, ValueError) as error:
        raise SeoulCultureApiError(
            'culture_api_invalid_response',
            'Seoul culture API response did not contain a valid total count.',
        ) from error

    rows = service_payload.get('row', [])
    if rows is None:
        rows = []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise SeoulCultureApiError(
            'culture_api_invalid_response',
            'Seoul culture API response rows had an unexpected shape.',
        )

    return total_count, tuple(rows)
