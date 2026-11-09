"""Import the reviewed SQLite place catalog into PostgreSQL without importing events."""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

PLACE_CATEGORY_MAPPING = {
    'museum': 'MUSEUM',
    'art_museum': 'ART_GALLERY',
    'arboretum': 'BOTANICAL_GARDEN',
    'botanical_garden': 'BOTANICAL_GARDEN',
    'botanical_park': 'BOTANICAL_GARDEN',
    'park': 'PARK',
    'cultural_space': 'CULTURAL_SPACE',
}

PLACE_UPSERT_SQL = """INSERT INTO places (
    place_id, category, name, road_address, lot_address, district,
    latitude, longitude, phone, website, managing_org, description, facilities,
    opening_hours, closed_days, admission_info, subtype, source_count,
    primary_source_id, source_url, last_collected_at
) VALUES (
    %(place_id)s, %(category)s, %(name)s, %(road_address)s, %(lot_address)s,
    %(district)s, %(latitude)s, %(longitude)s, %(phone)s, %(website)s,
    %(managing_org)s, %(description)s, %(facilities)s, %(opening_hours)s,
    %(closed_days)s, %(admission_info)s, %(subtype)s, %(source_count)s,
    %(primary_source_id)s, %(source_url)s, %(last_collected_at)s
)
ON CONFLICT (place_id) DO UPDATE SET
    category = EXCLUDED.category,
    name = EXCLUDED.name,
    road_address = EXCLUDED.road_address,
    lot_address = EXCLUDED.lot_address,
    district = EXCLUDED.district,
    latitude = EXCLUDED.latitude,
    longitude = EXCLUDED.longitude,
    phone = EXCLUDED.phone,
    website = EXCLUDED.website,
    managing_org = EXCLUDED.managing_org,
    description = EXCLUDED.description,
    facilities = EXCLUDED.facilities,
    opening_hours = EXCLUDED.opening_hours,
    closed_days = EXCLUDED.closed_days,
    admission_info = EXCLUDED.admission_info,
    subtype = EXCLUDED.subtype,
    source_count = EXCLUDED.source_count,
    primary_source_id = EXCLUDED.primary_source_id,
    source_url = EXCLUDED.source_url,
    last_collected_at = EXCLUDED.last_collected_at,
    updated_at = now()"""

SOURCE_RECORD_UPSERT_SQL = """INSERT INTO source_records (
    source_record_id, place_id, source_id, source_native_id, raw_name,
    raw_address, source_reference_date, source_url
) VALUES (
    %(source_record_id)s, %(place_id)s, %(source_id)s, %(source_native_id)s,
    %(raw_name)s, %(raw_address)s, %(source_reference_date)s, %(source_url)s
)
ON CONFLICT (source_record_id) DO UPDATE SET
    place_id = EXCLUDED.place_id,
    source_id = EXCLUDED.source_id,
    source_native_id = EXCLUDED.source_native_id,
    raw_name = EXCLUDED.raw_name,
    raw_address = EXCLUDED.raw_address,
    source_reference_date = EXCLUDED.source_reference_date,
    source_url = EXCLUDED.source_url"""

PLACE_ZONE_UPSERT_SQL = """INSERT INTO place_zones (
    zone_id, place_id, zone_name, zone_type, open_rule, hours_regular,
    hours_winter, last_entry, closed_days, adult_fee_krw, youth_fee_krw,
    child_fee_krw, source_id, source_url, verified_at
) VALUES (
    %(zone_id)s, %(place_id)s, %(zone_name)s, %(zone_type)s, %(open_rule)s,
    %(hours_regular)s, %(hours_winter)s, %(last_entry)s, %(closed_days)s,
    %(adult_fee_krw)s, %(youth_fee_krw)s, %(child_fee_krw)s, %(source_id)s,
    %(source_url)s, %(verified_at)s
)
ON CONFLICT (zone_id) DO UPDATE SET
    place_id = EXCLUDED.place_id,
    zone_name = EXCLUDED.zone_name,
    zone_type = EXCLUDED.zone_type,
    open_rule = EXCLUDED.open_rule,
    hours_regular = EXCLUDED.hours_regular,
    hours_winter = EXCLUDED.hours_winter,
    last_entry = EXCLUDED.last_entry,
    closed_days = EXCLUDED.closed_days,
    adult_fee_krw = EXCLUDED.adult_fee_krw,
    youth_fee_krw = EXCLUDED.youth_fee_krw,
    child_fee_krw = EXCLUDED.child_fee_krw,
    source_id = EXCLUDED.source_id,
    source_url = EXCLUDED.source_url,
    verified_at = EXCLUDED.verified_at"""


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized_value = str(value).strip()
    return normalized_value or None


def parse_collected_at(value: Any) -> datetime:
    normalized_value = normalize_text(value)
    if normalized_value is None:
        raise ValueError('A place row has no last_collected_at timestamp.')
    return datetime.fromisoformat(normalized_value.replace('Z', '+00:00'))


def parse_coordinate(value: Any, minimum: float, maximum: float) -> float | None:
    if value is None or value == '':
        return None
    try:
        coordinate = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(coordinate) or not minimum <= coordinate <= maximum:
        return None
    return coordinate


def map_place_category(source_place_type: str) -> str:
    try:
        return PLACE_CATEGORY_MAPPING[source_place_type]
    except KeyError as error:
        raise ValueError(
            f'Unsupported place_type in source catalog: {source_place_type}'
        ) from error


def load_source_catalog(
    source_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if not source_path.is_file():
        raise FileNotFoundError(f'Source SQLite file not found: {source_path}')

    source_uri = f'{source_path.resolve().as_uri()}?mode=ro'
    source_connection = sqlite3.connect(source_uri, uri=True)
    source_connection.row_factory = sqlite3.Row
    try:
        integrity_result = source_connection.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity_result != 'ok':
            raise ValueError('Source SQLite integrity_check did not return ok.')
        foreign_key_errors = source_connection.execute('PRAGMA foreign_key_check').fetchall()
        if foreign_key_errors:
            raise ValueError('Source SQLite contains foreign-key violations.')

        raw_place_rows = source_connection.execute(
            'SELECT * FROM places ORDER BY place_id'
        ).fetchall()
        raw_source_rows = source_connection.execute(
            'SELECT * FROM source_records ORDER BY source_reference_date DESC, source_record_id'
        ).fetchall()
        raw_zone_rows = source_connection.execute(
            'SELECT * FROM place_zones ORDER BY zone_id'
        ).fetchall()

        primary_source_by_place = {
            str(place_row['place_id']): str(place_row['primary_source_id'])
            for place_row in raw_place_rows
        }
        source_urls_by_place: dict[str, str] = {}
        for source_row in raw_source_rows:
            place_id = str(source_row['place_id'])
            source_url = normalize_text(source_row['source_url'])
            if (
                source_row['source_id'] == primary_source_by_place.get(place_id)
                and source_url is not None
            ):
                source_urls_by_place.setdefault(place_id, source_url)

        place_rows: list[dict[str, Any]] = []
        source_place_ids: set[str] = set()
        category_counts: Counter[str] = Counter()
        for source_row in raw_place_rows:
            source_place_id = str(source_row['place_id'])
            category = map_place_category(str(source_row['place_type']))
            place_rows.append({
                'place_id': source_place_id,
                'category': category,
                'name': normalize_text(source_row['name']) or '',
                'road_address': normalize_text(source_row['road_address']),
                'lot_address': normalize_text(source_row['lot_address']),
                'district': normalize_text(source_row['district']),
                'latitude': parse_coordinate(source_row['latitude'], -90, 90),
                'longitude': parse_coordinate(source_row['longitude'], -180, 180),
                'phone': normalize_text(source_row['phone']),
                'website': normalize_text(source_row['website']),
                'managing_org': normalize_text(source_row['managing_org']),
                'description': normalize_text(source_row['description']),
                'facilities': normalize_text(source_row['facilities']),
                'opening_hours': normalize_text(source_row['opening_hours']),
                'closed_days': normalize_text(source_row['closed_days']),
                'admission_info': normalize_text(source_row['admission_info']),
                'subtype': normalize_text(source_row['subtype']),
                'source_count': int(source_row['source_count']),
                'primary_source_id': str(source_row['primary_source_id']),
                'source_url': source_urls_by_place.get(source_place_id) or None,
                'last_collected_at': parse_collected_at(source_row['last_collected_at']),
            })
            source_place_ids.add(source_place_id)
            category_counts[category] += 1

        source_record_rows = [
            {
                'source_record_id': str(source_row['source_record_id']),
                'place_id': str(source_row['place_id']),
                'source_id': str(source_row['source_id']),
                'source_native_id': normalize_text(source_row['source_native_id']),
                'raw_name': normalize_text(source_row['raw_name']) or '',
                'raw_address': normalize_text(source_row['raw_address']),
                'source_reference_date': normalize_text(source_row['source_reference_date']),
                'source_url': normalize_text(source_row['source_url']) or '',
            }
            for source_row in raw_source_rows
            if str(source_row['place_id']) in source_place_ids
        ]
        place_zone_rows = [
            {
                'zone_id': str(zone_row['zone_id']),
                'place_id': str(zone_row['place_id']),
                'zone_name': normalize_text(zone_row['zone_name']) or '',
                'zone_type': normalize_text(zone_row['zone_type']) or '',
                'open_rule': normalize_text(zone_row['open_rule']),
                'hours_regular': normalize_text(zone_row['hours_regular']),
                'hours_winter': normalize_text(zone_row['hours_winter']),
                'last_entry': normalize_text(zone_row['last_entry']),
                'closed_days': normalize_text(zone_row['closed_days']),
                'adult_fee_krw': parse_integer(zone_row['adult_fee_krw']),
                'youth_fee_krw': parse_integer(zone_row['youth_fee_krw']),
                'child_fee_krw': parse_integer(zone_row['child_fee_krw']),
                'source_id': str(zone_row['source_id']),
                'source_url': normalize_text(zone_row['source_url']) or '',
                'verified_at': normalize_text(zone_row['verified_at']) or '',
            }
            for zone_row in raw_zone_rows
            if str(zone_row['place_id']) in source_place_ids
        ]
    finally:
        source_connection.close()

    return place_rows, source_record_rows, place_zone_rows


def parse_integer(value: Any) -> int | None:
    if value is None or value == '':
        return None
    return int(value)


def connect_database() -> Any:
    import psycopg

    database_password = os.environ.get('DATABASE_PASSWORD')
    if not database_password:
        raise RuntimeError('DATABASE_PASSWORD is not configured.')
    return psycopg.connect(
        host=os.environ.get('DATABASE_HOST', 'db'),
        port=os.environ.get('DATABASE_PORT', '5432'),
        dbname=os.environ.get('DATABASE_NAME', 'seoul_wave'),
        user=os.environ.get('DATABASE_USER', 'seoul_wave_app'),
        password=database_password,
        connect_timeout=10,
    )


def import_catalog(
    connection: Any,
    place_rows: list[dict[str, Any]],
    source_record_rows: list[dict[str, Any]],
    place_zone_rows: list[dict[str, Any]],
) -> None:
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.executemany(PLACE_UPSERT_SQL, place_rows)
            cursor.executemany(SOURCE_RECORD_UPSERT_SQL, source_record_rows)
            cursor.executemany(PLACE_ZONE_UPSERT_SQL, place_zone_rows)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Import reviewed places, source records, and zones into PostgreSQL.'
    )
    parser.add_argument('--source', required=True, type=Path, help='Path to seoul_places.sqlite3')
    parser.add_argument('--dry-run', action='store_true', help='Validate and summarize without connecting to PostgreSQL.')
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    place_rows, source_record_rows, place_zone_rows = load_source_catalog(arguments.source)
    category_counts = Counter(row['category'] for row in place_rows)

    if not arguments.dry_run:
        with connect_database() as connection:
            import_catalog(connection, place_rows, source_record_rows, place_zone_rows)

    print(json.dumps({
        'status': 'validated' if arguments.dry_run else 'imported',
        'source_file': arguments.source.name,
        'places': len(place_rows),
        'source_records': len(source_record_rows),
        'place_zones': len(place_zone_rows),
        'category_counts': dict(sorted(category_counts.items())),
        'events_imported': 0,
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
