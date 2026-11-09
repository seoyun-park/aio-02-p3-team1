"""Collect every aiViewCulture page and persist an auditable snapshot."""

from __future__ import annotations

import json
import math
import os
import sys
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import Request, urlopen

import psycopg
from psycopg.types.json import Jsonb


SERVICE_NAME = "aiViewCulture"
DATASET_ID = "OA-23041"
API_BASE_URL = "http://openapi.seoul.go.kr:8088"
PAGE_SIZE = 1000  # Successful live smoke-call size; API maximum is not assumed.
EXPECTED_FIELDS = {
    "CODENAME", "GUNAME", "CGG_CD", "TITLE", "DATE", "PLACE", "ORG_NAME",
    "USE_TRGT", "USE_FEE", "INQUIRY", "PLAYER", "PROGRAM", "ETC_DESC",
    "ORG_LINK", "MAIN_IMG", "RGSTDATE", "TICKET", "STRTDATE", "END_DATE",
    "THEMECODE", "LOT", "LAT", "IS_FREE", "HMPG_ADDR", "PRO_TIME",
}
COMPARISON_FIELDS = (
    "title", "place", "organizer_name", "category", "start_date_text", "end_date_text"
)


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized_value = str(value).strip()
    return normalized_value or None


def parse_coordinate(value: Any, minimum: Decimal, maximum: Decimal) -> Decimal | None:
    normalized_value = normalize_text(value)
    if normalized_value is None:
        return None
    try:
        coordinate = Decimal(normalized_value)
    except InvalidOperation:
        return None
    if not coordinate.is_finite() or coordinate < minimum or coordinate > maximum:
        return None
    return coordinate


def parse_cultcode(homepage_url: Any) -> tuple[str | None, str | None]:
    normalized_url = normalize_text(homepage_url)
    if normalized_url is None:
        return None, "homepage_missing"
    try:
        parsed_url = urlsplit(normalized_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            return None, "parse_failure"
        matching_values = [
            value for name, value in parse_qsl(parsed_url.query, keep_blank_values=True)
            if name == "cultcode"
        ]
    except (ValueError, UnicodeError):
        return None, "parse_failure"
    if not matching_values:
        return None, "missing"
    if len(matching_values) != 1:
        return None, "parse_failure"
    candidate = matching_values[0].strip()
    if not candidate:
        return None, "blank"
    return candidate, None


def make_event_values(
    event_id: uuid.UUID,
    collection_run_id: uuid.UUID,
    record_number: int,
    row: dict[str, Any],
) -> tuple[tuple[Any, ...], tuple[Any, ...], list[str], bool]:
    homepage_value = row.get("HMPG_ADDR")
    homepage_url = None if homepage_value is None else str(homepage_value)
    source_cultcode, cultcode_issue = parse_cultcode(homepage_value)
    title = normalize_text(row.get("TITLE"))
    fee_text = normalize_text(row.get("USE_FEE"))
    free_value = normalize_text(row.get("IS_FREE"))
    if free_value == "무료":
        free_status = "free"
    elif free_value == "유료" and fee_text is not None:
        free_status = "paid_with_details"
    elif free_value == "유료":
        free_status = "paid_details_missing"
    else:
        free_status = "unknown"

    longitude = parse_coordinate(row.get("LOT"), Decimal("-180"), Decimal("180"))
    latitude = parse_coordinate(row.get("LAT"), Decimal("-90"), Decimal("90"))
    coordinate_parse_failed = (
        normalize_text(row.get("LOT")) is not None and longitude is None
    ) or (normalize_text(row.get("LAT")) is not None and latitude is None)
    missing_fields = sorted(EXPECTED_FIELDS.difference(row.keys()))

    event_values = (
        event_id,
        collection_run_id,
        record_number,
        source_cultcode,
        "candidate",
        title,
        normalize_text(row.get("CODENAME")),
        normalize_text(row.get("GUNAME")),
        normalize_text(row.get("CGG_CD")),
        normalize_text(row.get("STRTDATE")),
        normalize_text(row.get("END_DATE")),
        normalize_text(row.get("DATE")),
        normalize_text(row.get("PLACE")),
        normalize_text(row.get("ORG_NAME")),
        normalize_text(row.get("USE_TRGT")),
        fee_text,
        free_status,
        normalize_text(row.get("INQUIRY")),
        normalize_text(row.get("PLAYER")),
        normalize_text(row.get("PROGRAM")),
        normalize_text(row.get("ETC_DESC")),
        normalize_text(row.get("ORG_LINK")),
        normalize_text(row.get("MAIN_IMG")),
        normalize_text(row.get("RGSTDATE")),
        normalize_text(row.get("TICKET")),
        normalize_text(row.get("THEMECODE")),
        normalize_text(row.get("PRO_TIME")),
        latitude,
        longitude,
        homepage_url,
    )
    source_values = (
        uuid.uuid4(),
        event_id,
        collection_run_id,
        record_number,
        DATASET_ID,
        SERVICE_NAME,
        homepage_url,
        Jsonb(row),
    )
    issue_codes = [cultcode_issue] if cultcode_issue else []
    return event_values, source_values, issue_codes, coordinate_parse_failed or bool(missing_fields)


def get_api_page(api_key: str, start_index: int, end_index: int) -> tuple[dict[str, Any], dict[str, Any]]:
    encoded_key = urlencode({"key": api_key}).split("=", 1)[1]
    request_url = (
        f"{API_BASE_URL}/{encoded_key}/json/{SERVICE_NAME}/"
        f"{start_index}/{end_index}"
    )
    request = Request(request_url, headers={"User-Agent": "seoul-wave-event-collector/1.0"})
    last_code = "REQUEST_FAILED"
    for attempt_number in range(3):
        try:
            with urlopen(request, timeout=45) as response:
                decoded_body = response.read().decode("utf-8-sig")
            response_body = json.loads(decoded_body)
            service_payload = response_body.get(SERVICE_NAME)
            if not isinstance(service_payload, dict):
                raise ValueError("unexpected_response_shape")
            return response_body, service_payload
        except HTTPError as error:
            last_code = f"HTTP_{error.code}"
        except (URLError, TimeoutError, json.JSONDecodeError, UnicodeError, ValueError) as error:
            last_code = type(error).__name__.upper()
        if attempt_number < 2:
            time.sleep(1 + attempt_number)
    raise RuntimeError(last_code)


def connect_database() -> psycopg.Connection:
    required_values = {
        "host": os.environ.get("DATABASE_HOST", "db"),
        "port": os.environ.get("DATABASE_PORT", "5432"),
        "dbname": os.environ.get("DATABASE_NAME", "seoul_wave"),
        "user": os.environ.get("DATABASE_USER", "seoul_wave_app"),
        "password": os.environ.get("DATABASE_PASSWORD"),
    }
    if not required_values["password"]:
        raise RuntimeError("DATABASE_PASSWORD is not configured")
    return psycopg.connect(**required_values, connect_timeout=10)


def response_result(service_payload: dict[str, Any]) -> tuple[str | None, str | None]:
    result = service_payload.get("RESULT")
    if not isinstance(result, dict):
        return None, None
    return normalize_text(result.get("CODE")), normalize_text(result.get("MESSAGE"))


def persist_page(
    connection: psycopg.Connection,
    run_id: uuid.UUID,
    page_number: int,
    start_index: int,
    end_index: int,
    result_code: str | None,
    result_message: str | None,
    total_count: int | None,
    rows: list[Any],
    page_status: str,
    error_code: str | None,
    page_size: int,
) -> tuple[int, int, Counter[str], list[dict[str, Any]], dict[str, list[dict[str, Any]]], list[str]]:
    valid_rows = [row for row in rows if isinstance(row, dict)]
    counters: Counter[str] = Counter()
    issue_rows: list[dict[str, Any]] = []
    cultcode_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    missing_field_names: list[str] = []
    event_arguments: list[tuple[Any, ...]] = []
    source_arguments: list[tuple[Any, ...]] = []
    collected_at = datetime.now(timezone.utc)

    for offset, row in enumerate(valid_rows):
        record_number = start_index + offset
        event_id = uuid.uuid4()
        event_values, source_values, issue_codes, record_has_schema_issue = make_event_values(
            event_id, run_id, record_number, row
        )
        event_arguments.append(event_values)
        source_arguments.append(source_values)
        if record_has_schema_issue:
            absent_fields = sorted(EXPECTED_FIELDS.difference(row.keys()))
            if absent_fields:
                counters["schema_error_count"] += 1
                missing_field_names.extend(absent_fields)
                issue_rows.append({"record_number": record_number, "title": normalize_text(row.get("TITLE")), "issue": "missing_expected_fields", "fields": absent_fields})
            for key in ("LOT", "LAT"):
                raw_value = normalize_text(row.get(key))
                if raw_value is not None and parse_coordinate(
                    raw_value,
                    Decimal("-180") if key == "LOT" else Decimal("-90"),
                    Decimal("180") if key == "LOT" else Decimal("90"),
                ) is None:
                    counters["coordinate_parse_failure_count"] += 1
                    issue_rows.append({"record_number": record_number, "title": normalize_text(row.get("TITLE")), "issue": "coordinate_parse_failure", "field": key})
        if issue_codes:
            issue_code = issue_codes[0]
            counter_name = {
                "homepage_missing": "homepage_missing_count",
                "missing": "cultcode_missing_count",
                "blank": "cultcode_blank_count",
                "parse_failure": "cultcode_parse_failure_count",
            }[issue_code]
            counters[counter_name] += 1
            issue_rows.append({"record_number": record_number, "title": normalize_text(row.get("TITLE")), "issue": issue_code})
        cultcode = event_values[3]
        if cultcode is not None:
            cultcode_rows[cultcode].append({"record_number": record_number, "title": normalize_text(row.get("TITLE"))})

    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO collection_run_pages (
                       collection_run_id, page_number, requested_start_index,
                       requested_end_index, result_code, result_message,
                       response_list_total_count, received_record_count,
                       page_status, error_code, sanitized_error, requested_at, completed_at
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    run_id, page_number, start_index, end_index, result_code,
                    result_message, total_count, len(rows), page_status,
                    error_code, error_code, collected_at, datetime.now(timezone.utc),
                ),
            )
            if event_arguments:
                cursor.executemany(
                    """INSERT INTO events (
                           event_id, collection_run_id, source_record_number,
                           source_cultcode, source_identity_status, title, category,
                           district_name, district_code, start_date_text, end_date_text,
                           event_date_text, place, organizer_name, target_audience,
                           fee_text, free_status, inquiry, player, program_text,
                           description_text, organization_url, main_image_url,
                           registration_date_text, ticket_text, theme_code,
                           program_time_text, latitude, longitude, culture_detail_url
                       ) VALUES (
                           %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                       )""",
                    event_arguments,
                )
                cursor.executemany(
                    """INSERT INTO event_source_records (
                           event_source_record_id, event_id, collection_run_id,
                           source_record_number, source_dataset_id, source_service_name,
                           collected_at, culture_detail_url, raw_payload
                       ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [
                        values[:6] + (collected_at,) + values[6:]
                        for values in source_arguments
                    ],
                )

    return len(valid_rows), len(rows), counters, issue_rows, cultcode_rows, missing_field_names


def comparison_key(row: dict[str, Any]) -> tuple[str, ...] | None:
    values = tuple(row.get(field) for field in COMPARISON_FIELDS)
    if any(value is None or not str(value).strip() for value in values):
        return None
    return tuple(str(value).strip() for value in values)


def compare_with_prior_dates(
    connection: psycopg.Connection,
    current_run_id: uuid.UUID,
    current_records: list[dict[str, Any]],
) -> dict[str, Any]:
    comparisons: list[dict[str, Any]] = []
    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT DISTINCT ON ((started_at AT TIME ZONE 'UTC')::date)
                       collection_run_id, (started_at AT TIME ZONE 'UTC')::date AS run_date
                 FROM collection_runs
                WHERE status = 'succeeded' AND snapshot_promoted
                  AND collection_run_id <> %s
                  AND (started_at AT TIME ZONE 'UTC')::date < (
                        SELECT (started_at AT TIME ZONE 'UTC')::date
                          FROM collection_runs WHERE collection_run_id = %s
                  )
                ORDER BY (started_at AT TIME ZONE 'UTC')::date DESC, completed_at DESC""",
            (current_run_id, current_run_id),
        )
        prior_runs = cursor.fetchall()

    if not prior_runs:
        return {"status": "no_prior_complete_snapshot_on_another_date", "comparisons": []}

    for prior_run_id, prior_date in prior_runs:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT title, place, organizer_name, category, start_date_text,
                          end_date_text, source_cultcode, source_record_number
                     FROM events WHERE collection_run_id = %s""",
                (prior_run_id,),
            )
            prior_records = [
                {
                    "title": row[0], "place": row[1], "organizer_name": row[2],
                    "category": row[3], "start_date_text": row[4],
                    "end_date_text": row[5], "source_cultcode": row[6],
                    "record_number": row[7],
                }
                for row in cursor.fetchall()
            ]

        prior_by_key: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        current_by_key: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for record in prior_records:
            key = comparison_key(record)
            if key is not None:
                prior_by_key[key].append(record)
        for record in current_records:
            key = comparison_key(record)
            if key is not None:
                current_by_key[key].append(record)

        shared_keys = set(prior_by_key).intersection(current_by_key)
        unique_matches = [
            (prior_by_key[key][0], current_by_key[key][0])
            for key in shared_keys
            if len(prior_by_key[key]) == 1 and len(current_by_key[key]) == 1
        ]
        ambiguous_shared_keys = [
            key for key in shared_keys
            if len(prior_by_key[key]) != 1 or len(current_by_key[key]) != 1
        ]
        same_code = sum(
            before["source_cultcode"] is not None
            and before["source_cultcode"] == after["source_cultcode"]
            for before, after in unique_matches
        )
        changed_code = sum(
            before["source_cultcode"] is not None
            and after["source_cultcode"] is not None
            and before["source_cultcode"] != after["source_cultcode"]
            for before, after in unique_matches
        )
        missing_code = sum(
            before["source_cultcode"] is None or after["source_cultcode"] is None
            for before, after in unique_matches
        )
        comparisons.append({
            "prior_collection_run_id": str(prior_run_id),
            "prior_date_utc": prior_date.isoformat(),
            "prior_record_count": len(prior_records),
            "current_record_count": len(current_records),
            "unique_comparison_matches": len(unique_matches),
            "same_cultcode_count": same_code,
            "changed_cultcode_count": changed_code,
            "missing_cultcode_in_match_count": missing_code,
            "ambiguous_shared_key_count": len(ambiguous_shared_keys),
            "unmatched_prior_candidate_count": max(0, len(prior_by_key) - len(shared_keys)),
            "unmatched_current_candidate_count": max(0, len(current_by_key) - len(shared_keys)),
            "comparison_fields": list(COMPARISON_FIELDS),
            "warning": "comparison fields are review aids only; they are not identifiers or deduplication keys",
        })
    return {"status": "review_required", "comparisons": comparisons}


def main() -> int:
    api_key = os.environ.get("SEOUL_CULTURE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("SEOUL_CULTURE_API_KEY is not configured")

    page_size = PAGE_SIZE
    run_id = uuid.uuid4()
    report: dict[str, Any] = {
        "collection_run_id": str(run_id),
        "dataset_id": DATASET_ID,
        "service_name": SERVICE_NAME,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "page_size": page_size,
        "identity_status": "candidate",
        "cultcode_is_officially_defined": False,
        "api_key_included": False,
    }
    page_reports: list[dict[str, Any]] = []
    issue_rows: list[dict[str, Any]] = []
    all_records: list[dict[str, Any]] = []
    missing_field_counter: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    cultcode_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    result_list_total_count: int | None = None
    first_result_code: str | None = None
    first_result_message: str | None = None
    expected_page_count = 0
    successful_page_count = 0
    failed_page_count = 0
    collected_any_page = False
    connection = connect_database()
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO collection_runs (
                           collection_run_id, source_dataset_id, source_service_name,
                           status, identity_status, requested_start_index,
                           requested_page_size
                       ) VALUES (%s, %s, %s, 'running', 'candidate', 1, %s)""",
                    (run_id, DATASET_ID, SERVICE_NAME, page_size),
                )

        page_number = 1
        start_index = 1
        first_page_payload: dict[str, Any] | None = None
        first_page_rows: list[Any] = []
        try:
            _, first_page_payload = get_api_page(api_key, 1, page_size)
            result_list_total_count = int(first_page_payload.get("list_total_count"))
            first_result_code, first_result_message = response_result(first_page_payload)
            first_page_rows = first_page_payload.get("row") or []
            if not isinstance(first_page_rows, list):
                raise RuntimeError("INVALID_ROW_LIST")
            collected_any_page = True
            expected_page_count = max(1, math.ceil(result_list_total_count / page_size))
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """UPDATE collection_runs
                              SET response_list_total_count = %s,
                                  response_result_code = %s,
                                  response_result_message = %s,
                                  expected_page_count = %s
                            WHERE collection_run_id = %s""",
                        (result_list_total_count, first_result_code, first_result_message, expected_page_count, run_id),
                    )
        except (RuntimeError, TypeError, ValueError) as error:
            failure_code = str(error) if str(error).isupper() and " " not in str(error) else type(error).__name__.upper()
            persist_page(
                connection, run_id, 1, 1, page_size, None, None, None, [],
                "failed", failure_code, page_size,
            )
            failed_page_count = 1
            page_reports.append({"page_number": 1, "start_index": 1, "end_index": page_size, "status": "failed", "error_code": failure_code})
            expected_page_count = 1

        if collected_any_page:
            for page_number in range(1, expected_page_count + 1):
                start_index = (page_number - 1) * page_size + 1
                end_index = max(start_index, min(page_number * page_size, result_list_total_count or 0))
                if page_number == 1:
                    service_payload = first_page_payload or {}
                    rows = first_page_rows
                else:
                    try:
                        _, service_payload = get_api_page(api_key, start_index, end_index)
                        rows = service_payload.get("row") or []
                        if not isinstance(rows, list):
                            raise RuntimeError("INVALID_ROW_LIST")
                    except (RuntimeError, TypeError, ValueError) as error:
                        failure_code = str(error) if str(error).isupper() and " " not in str(error) else type(error).__name__.upper()
                        persist_page(
                            connection, run_id, page_number, start_index, end_index,
                            None, None, result_list_total_count, [], "failed", failure_code, page_size,
                        )
                        failed_page_count += 1
                        page_reports.append({"page_number": page_number, "start_index": start_index, "end_index": end_index, "status": "failed", "error_code": failure_code})
                        continue

                result_code, result_message = response_result(service_payload)
                page_total = service_payload.get("list_total_count")
                try:
                    page_total = int(page_total)
                except (TypeError, ValueError):
                    page_total = None
                valid_row_count = sum(isinstance(row, dict) for row in rows)
                expected_row_count = max(0, min(page_size, (result_list_total_count or 0) - start_index + 1))
                page_is_complete = (
                    result_code == "INFO-000"
                    and page_total == result_list_total_count
                    and len(rows) == expected_row_count
                    and valid_row_count == len(rows)
                )
                page_status = "succeeded" if page_is_complete else "failed"
                error_code = None
                if not page_is_complete:
                    if result_code != "INFO-000":
                        error_code = result_code or "MISSING_RESULT_CODE"
                    elif page_total != result_list_total_count:
                        error_code = "INCONSISTENT_LIST_TOTAL_COUNT"
                    elif valid_row_count != len(rows):
                        error_code = "INVALID_ROW_OBJECT"
                    else:
                        error_code = "UNEXPECTED_PAGE_ROW_COUNT"

                saved_count, received_count, page_counts, page_issues, page_codes, missing_fields = persist_page(
                    connection, run_id, page_number, start_index, end_index,
                    result_code, result_message, page_total, rows, page_status,
                    error_code, page_size,
                )
                all_records.extend(
                    {
                        "title": normalize_text(row.get("TITLE")),
                        "place": normalize_text(row.get("PLACE")),
                        "organizer_name": normalize_text(row.get("ORG_NAME")),
                        "category": normalize_text(row.get("CODENAME")),
                        "start_date_text": normalize_text(row.get("STRTDATE")),
                        "end_date_text": normalize_text(row.get("END_DATE")),
                        "source_cultcode": parse_cultcode(row.get("HMPG_ADDR"))[0],
                        "record_number": start_index + offset,
                    }
                    for offset, row in enumerate(rows)
                    if isinstance(row, dict)
                )
                totals["saved_record_count"] += saved_count
                totals["received_record_count"] += received_count
                totals.update(page_counts)
                issue_rows.extend(page_issues)
                missing_field_counter.update(missing_fields)
                for code, references in page_codes.items():
                    cultcode_rows[code].extend(references)
                if page_status == "succeeded":
                    successful_page_count += 1
                else:
                    failed_page_count += 1
                page_reports.append({
                    "page_number": page_number,
                    "start_index": start_index,
                    "end_index": end_index,
                    "result_code": result_code,
                    "list_total_count": page_total,
                    "received_record_count": received_count,
                    "saved_record_count": saved_count,
                    "status": page_status,
                    "error_code": error_code,
                })
                time.sleep(0.2)

        duplicate_groups = [
            {"cultcode": code, "record_count": len(references), "rows": references}
            for code, references in sorted(cultcode_rows.items())
            if len(references) > 1
        ]
        duplicate_record_count = sum(group["record_count"] for group in duplicate_groups)
        totals["cultcode_duplicate_group_count"] = len(duplicate_groups)
        totals["cultcode_duplicate_record_count"] = duplicate_record_count
        totals["schema_error_count"] = sum(1 for row in issue_rows if row["issue"] == "missing_expected_fields")
        totals["coordinate_parse_failure_count"] = sum(1 for row in issue_rows if row["issue"] == "coordinate_parse_failure")
        is_complete = (
            collected_any_page
            and failed_page_count == 0
            and successful_page_count == expected_page_count
            and result_list_total_count == totals["saved_record_count"]
            and result_list_total_count == totals["received_record_count"]
        )
        final_status = "succeeded" if is_complete else ("partial" if totals["saved_record_count"] else "failed")
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    """UPDATE collection_runs
                          SET status = %s,
                              snapshot_promoted = %s,
                              identity_status = 'candidate',
                              completed_at = now(),
                              expected_page_count = %s,
                              successful_page_count = %s,
                              failed_page_count = %s,
                              received_record_count = %s,
                              saved_record_count = %s,
                              homepage_missing_count = %s,
                              cultcode_missing_count = %s,
                              cultcode_blank_count = %s,
                              cultcode_parse_failure_count = %s,
                              cultcode_duplicate_group_count = %s,
                              cultcode_duplicate_record_count = %s,
                              coordinate_parse_failure_count = %s,
                              schema_error_count = %s
                        WHERE collection_run_id = %s""",
                    (
                        final_status, is_complete, expected_page_count,
                        successful_page_count, failed_page_count,
                        totals["received_record_count"], totals["saved_record_count"],
                        totals["homepage_missing_count"], totals["cultcode_missing_count"],
                        totals["cultcode_blank_count"], totals["cultcode_parse_failure_count"],
                        totals["cultcode_duplicate_group_count"], totals["cultcode_duplicate_record_count"],
                        totals["coordinate_parse_failure_count"], totals["schema_error_count"], run_id,
                    ),
                )

        comparison = compare_with_prior_dates(connection, run_id, all_records) if is_complete else {
            "status": "not_run_current_snapshot_incomplete", "comparisons": []
        }
        report.update({
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "snapshot_status": final_status,
            "snapshot_promoted": is_complete,
            "identity_status": "candidate",
            "requested_scope": {"start_index": 1, "end_index": result_list_total_count, "page_size": page_size},
            "list_total_count": result_list_total_count,
            "received_record_count": totals["received_record_count"],
            "saved_record_count": totals["saved_record_count"],
            "page_counts": {"expected": expected_page_count, "successful": successful_page_count, "failed": failed_page_count},
            "quality_counts": {
                key: totals[key] for key in (
                    "homepage_missing_count", "cultcode_missing_count", "cultcode_blank_count",
                    "cultcode_parse_failure_count", "cultcode_duplicate_group_count",
                    "cultcode_duplicate_record_count", "coordinate_parse_failure_count", "schema_error_count",
                )
            },
            "missing_expected_field_occurrences": dict(sorted(missing_field_counter.items())),
            "page_results": page_reports,
            "issue_rows": issue_rows,
            "duplicate_cultcodes": duplicate_groups,
            "date_comparison": comparison,
            "comparison_fields_are_identifiers": False,
            "cultcode_unique_constraint_applied": False,
            "identity_review_recommendation": (
                "Review verified status only after all batch issues are resolved and at least two complete snapshots from different dates show consistent cultcode values."
            ),
        })
        report_directory = Path(__file__).resolve().parents[1] / "data" / "processed"
        report_directory.mkdir(parents=True, exist_ok=True)
        report_path = report_directory / f"culture_collection_validation_{run_id}.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "collection_run_id": str(run_id),
            "status": final_status,
            "snapshot_promoted": is_complete,
            "list_total_count": result_list_total_count,
            "saved_record_count": totals["saved_record_count"],
            "pages": {"expected": expected_page_count, "successful": successful_page_count, "failed": failed_page_count},
            "quality_counts": report["quality_counts"],
            "identity_status": "candidate",
            "report_path": str(report_path),
        }, ensure_ascii=False))
        return 0 if is_complete else 2
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # Never print exception text that could contain a credential-bearing request URL.
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
