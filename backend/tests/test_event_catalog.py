import json
from urllib.error import HTTPError, URLError

import pytest

from app.integrations import seoul_culture_api
from app.integrations.seoul_culture_api import SeoulCultureApiError
from app.services import event_service
from app.services.event_service import EventDataUnavailable


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


def api_response(result_code="INFO-000", **overrides):
    service = {"RESULT": {"CODE": result_code}, "list_total_count": 1, "row": []}
    service.update(overrides)
    return json.dumps({seoul_culture_api.SERVICE_NAME: service}).encode("utf-8")


def test_fetch_event_page_parses_success_and_encodes_api_key(monkeypatch):
    body = api_response(row=[{"TITLE": "행사"}])
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return FakeResponse(body)

    monkeypatch.setattr(seoul_culture_api, "urlopen", fake_urlopen)

    total_count, rows = seoul_culture_api.fetch_event_page("key/with space", 3, 7)

    assert total_count == 1
    assert rows == ({"TITLE": "행사"},)
    assert "key%2Fwith%20space" in seen["url"]
    assert seen["url"].endswith("/3/7")
    assert seen["timeout"] == seoul_culture_api.REQUEST_TIMEOUT_SECONDS


def test_fetch_event_page_handles_empty_result(monkeypatch):
    monkeypatch.setattr(
        seoul_culture_api,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(api_response("INFO-200")),
    )

    assert seoul_culture_api.fetch_event_page("key", 1, 10) == (0, ())


@pytest.mark.parametrize(
    "failure",
    [
        HTTPError("https://example.org", 503, "unavailable", None, None),
        URLError("offline"),
        TimeoutError("timeout"),
        OSError("socket error"),
        UnicodeError("decode error"),
    ],
)
def test_fetch_event_page_converts_transport_failures(monkeypatch, failure):
    def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(seoul_culture_api, "urlopen", fail)

    with pytest.raises(SeoulCultureApiError):
        seoul_culture_api.fetch_event_page("key", 1, 10)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"{}",
        api_response("INFO-999"),
        api_response("INFO-000", list_total_count="bad"),
        api_response("INFO-000", row="unexpected"),
        api_response("INFO-000", row=["unexpected"]),
    ],
)
def test_fetch_event_page_rejects_invalid_api_responses(monkeypatch, body):
    monkeypatch.setattr(
        seoul_culture_api,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(body),
    )

    with pytest.raises(SeoulCultureApiError):
        seoul_culture_api.fetch_event_page("key", 1, 10)


def test_event_service_requires_api_key(monkeypatch):
    monkeypatch.delenv("SEOUL_CULTURE_API_KEY", raising=False)

    with pytest.raises(EventDataUnavailable) as error:
        event_service.list_events(limit=10, offset=0)

    assert error.value.error_code == "culture_api_not_configured"


def test_event_service_maps_live_source_fields(monkeypatch):
    monkeypatch.setenv("SEOUL_CULTURE_API_KEY", "test-key")
    monkeypatch.setattr(
        event_service,
        "fetch_event_page",
        lambda **_kwargs: (
            1,
            ({
                "TITLE": "  전시 ",
                "CODENAME": "전시/미술",
                "GUNAME": "영등포구",
                "USE_FEE": "성인 5,000원",
                "IS_FREE": "유료",
                "LAT": "37.52",
                "LOT": "126.93",
                "HMPG_ADDR": "https://example.org/event",
            },),
        ),
    )

    collection = event_service.list_events(limit=5, offset=2)

    assert (collection.total_count, collection.limit, collection.offset) == (1, 5, 2)
    event = collection.events[0]
    assert event.title == "전시"
    assert event.free_status == "paid_with_details"
    assert (event.latitude, event.longitude) == (37.52, 126.93)


@pytest.mark.parametrize(
    ("free_value", "fee_text", "expected"),
    [
        ("무료", None, "free"),
        ("유료", None, "paid_details_missing"),
        ("미정", None, "unknown"),
        ("유료", "", "paid_details_missing"),
    ],
)
def test_event_free_status_and_invalid_coordinates_are_normalized(
    free_value, fee_text, expected
):
    event = event_service.map_source_row(
        {"IS_FREE": free_value, "USE_FEE": fee_text, "LAT": "999", "LOT": "nope"}
    )

    assert event.free_status == expected
    assert event.latitude is None
    assert event.longitude is None


def test_event_service_converts_upstream_api_errors(monkeypatch):
    monkeypatch.setenv("SEOUL_CULTURE_API_KEY", "test-key")

    def fail(**_kwargs):
        raise SeoulCultureApiError("culture_api_unreachable", "test outage")

    monkeypatch.setattr(event_service, "fetch_event_page", fail)

    with pytest.raises(EventDataUnavailable) as error:
        event_service.list_events(limit=10, offset=0)

    assert error.value.error_code == "culture_api_unreachable"
