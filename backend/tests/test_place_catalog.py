import os

import psycopg
import pytest

from app.repositories import place_repository
from app.services import place_service
from app.services.place_service import PlaceNotFound


@pytest.fixture
def place_connection():
    connection = psycopg.connect(
        host=os.environ["DATABASE_HOST"],
        port=os.environ["DATABASE_PORT"],
        dbname=os.environ["DATABASE_NAME"],
        user=os.environ["DATABASE_USER"],
        password=os.environ["DATABASE_PASSWORD"],
    )
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def seed_places(connection):
    connection.cursor().executemany(
        """INSERT INTO places (
               place_id, category, name, district, latitude, longitude,
               source_count, primary_source_id, last_collected_at, is_active
           ) VALUES (%s, %s, %s, %s, %s, %s, 1, %s, now(), %s)""",
        [
            ("ci-park", "PARK", "한강공원", "영등포구", 37.52, 126.93, "ci-source", True),
            ("ci-museum", "MUSEUM", "시립박물관", "종로구", 37.57, 126.98, "ci-source", True),
            ("ci-inactive", "PARK", "폐쇄 공원", "영등포구", 37.52, 126.93, "ci-source", False),
            ("ci-no-coordinates", "PARK", "좌표 미확인 공원", "영등포구", None, None, "ci-source", True),
        ],
    )


def test_place_repository_filters_active_rows_and_maps_results(place_connection):
    seed_places(place_connection)

    assert place_repository.count_active_places(place_connection, None, None) == 3
    assert place_repository.count_active_places(place_connection, "PARK", "영등포구") == 2

    places = place_repository.list_active_places(
        place_connection, "PARK", "영등포구", limit=10, offset=0
    )
    assert {place.place_id for place in places} == {"ci-no-coordinates", "ci-park"}
    park = next(place for place in places if place.place_id == "ci-park")
    assert park.latitude is not None


def test_recommendation_places_require_category_and_coordinates(place_connection):
    seed_places(place_connection)

    assert place_repository.list_active_recommendation_places(place_connection, ()) == ()
    places = place_repository.list_active_recommendation_places(
        place_connection, ("PARK",)
    )

    assert [place.place_id for place in places] == ["ci-park"]


def test_place_service_returns_active_place_and_reports_missing_place(place_connection):
    seed_places(place_connection)

    collection = place_service.list_places(
        place_connection,
        category="PARK",
        district="영등포구",
        limit=1,
        offset=0,
    )
    assert collection.total_count == 2
    assert len(collection.places) == 1

    place = place_service.get_place(place_connection, "ci-park")
    assert place.name == "한강공원"

    with pytest.raises(PlaceNotFound, match="장소를 찾을 수 없습니다"):
        place_service.get_place(place_connection, "ci-inactive")
