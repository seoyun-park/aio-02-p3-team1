"""Transcribe the published table, compare it with OCR, and estimate arrivals.

Run from the repository root: python backend/scripts/prepare_timetable.py
Uses data/raw inputs and writes data/processed/timetable.json and its validation report.
No directories are created. This regenerates the reviewed manual transcription
of this source image; it is not an automatic extractor for new timetables.
Arrival estimates use the selected origin departure plus the sum of diagram
durations. They do not join departures at different stops into a shared trip.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path



BACKEND_ROOT = Path(__file__).resolve().parent.parent
DATA_DIRECTORY = BACKEND_ROOT / "data"
RAW_DATA_DIRECTORY = DATA_DIRECTORY / "raw"
PROCESSED_DATA_DIRECTORY = DATA_DIRECTORY / "processed"
SOURCE_IMAGE_FILE = RAW_DATA_DIRECTORY / "timetable_source_20260815.jpg"
TIMETABLE_DATA_FILE = PROCESSED_DATA_DIRECTORY / "timetable.json"
VALIDATION_REPORT_FILE = PROCESSED_DATA_DIRECTORY / "timetable_validation.json"
REVIEW_DATE = "2026-11-03"
OFFICIAL_SCHEDULE_PAGE_URL = "https://www.hgbus.co.kr/itc/information/schedule?menuId=5"
OFFICIAL_SCHEDULE_IMAGE_URL = "https://www.hgbus.co.kr/images/sub/information/schedule_img4_v260815.jpg"


# These values were transcribed by visually reading the original image.
# Positions 1..12 are SOURCE TABLE rows, not trips or connections.
SOURCE_COLUMNS = [
    ("MAGOK", "여의도행", "departure", "YEOUIDO", 63.5,
     "11:20 12:20 13:20 14:20 15:00 15:30 16:00 17:00 17:40 18:40 - -"),
    ("MAGOK", "도착", "arrival", None, 143.5,
     "12:53 13:53 14:53 15:53 - - 17:33 18:33 19:13 20:13 21:09 21:39"),
    ("MANGWON", "여의도행", "departure", "YEOUIDO", 243.5,
     "11:42 12:42 13:42 14:42 15:22 15:52 16:22 17:22 18:02 19:02 - -"),
    ("MANGWON", "마곡행", "departure", "MAGOK", 323.5,
     "12:34 13:34 14:34 15:34 - - 17:14 18:14 18:54 19:54 20:48 21:18"),
    ("YEOUIDO", "마곡행", "departure", "MAGOK", 423.5,
     "12:12 13:12 14:12 15:12 - - 16:52 17:52 18:32 19:32 20:25 20:55"),
    ("YEOUIDO", "잠실행", "departure", "JAMSIL", 503.5,
     "12:38 13:35 14:40 15:40 16:00 16:30 17:08 18:05 19:05 20:05 - -"),
    ("APGUJEONG", "여의도행", "departure", "YEOUIDO", 603.5,
     "- 12:55 - 14:55 - - - 17:25 - 19:25 - 20:15"),
    ("APGUJEONG", "잠실행", "departure", "JAMSIL", 683.5,
     "- 14:07 - 16:12 - 17:02 - 18:37 - 20:37 - -"),
    ("OKSU", "여의도행", "departure", "YEOUIDO", 793.0,
     "11:52 - 13:52 - - - 16:22 - 18:22 - 19:42 -"),
    ("OKSU", "잠실행", "departure", "JAMSIL", 873.0,
     "13:13 - 15:15 - 16:35 - 17:43 - 19:40 - - -"),
    ("SEOUL_FOREST", "여의도행", "departure", "YEOUIDO", 982.0,
     "11:35 12:35 13:35 14:35 - - 16:05 17:05 18:05 19:05 19:25 19:55"),
    ("SEOUL_FOREST", "잠실행", "departure", "JAMSIL", 1062.0,
     "13:31 14:28 15:33 16:33 16:53 17:23 18:01 18:58 19:58 20:58 - -"),
    ("TTUKSEOM", "여의도행", "departure", "YEOUIDO", 1170.0,
     "11:17 12:17 13:17 14:17 - - 15:47 16:47 17:47 18:47 19:07 19:37"),
    ("TTUKSEOM", "잠실행", "departure", "JAMSIL", 1250.0,
     "13:49 14:46 15:51 16:51 17:11 17:41 18:19 19:16 20:16 21:16 - -"),
    ("JAMSIL", "여의도행", "departure", "YEOUIDO", 1350.0,
     "11:00 12:00 13:00 14:00 - - 15:30 16:30 17:30 18:30 18:50 19:20"),
    ("JAMSIL", "도착", "arrival", None, 1430.0,
     "14:03 15:00 16:05 17:05 17:25 17:55 18:33 19:30 20:30 21:30 - -"),
]

STOP_NAMES = [
    ("MAGOK", "마곡"),
    ("MANGWON", "망원"),
    ("YEOUIDO", "여의도"),
    ("APGUJEONG", "압구정"),
    ("OKSU", "옥수"),
    ("SEOUL_FOREST", "서울숲"),
    ("TTUKSEOM", "뚝섬"),
    ("JAMSIL", "잠실"),
]


def build_column_id(
    stop_id: str,
    event_type: str,
    direction_to_stop_id: str | None,
) -> str:
    if event_type == "departure":
        return f"{stop_id}_TO_{direction_to_stop_id}"
    return f"{stop_id}_ARRIVAL"


def normalize_ocr_time(raw_time_text: str) -> str | None:
    # OCR sometimes recognizes the colon as a dot or bullet, or omits it.
    # No digit substitutions or expected-value-based correction are performed.
    digits = re.sub(r"[^0-9]", "", raw_time_text)
    if len(digits) != 4:
        return None
    value = f"{digits[:2]}:{digits[2:]}"
    return value if re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) else None


def calculate_arrival_time(
    departure_time: str,
    travel_minutes: int,
) -> tuple[str, int]:
    """Return local clock time and days after the departure's service date."""
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", departure_time):
        raise ValueError(f"Invalid departure time: {departure_time}")
    if type(travel_minutes) is not int or travel_minutes < 0:
        raise ValueError("Travel minutes must be a nonnegative integer.")
    hours, minutes = map(int, departure_time.split(":"))
    days, minute_of_day = divmod(hours * 60 + minutes + travel_minutes, 24 * 60)
    hour, minute = divmod(minute_of_day, 60)
    return f"{hour:02d}:{minute:02d}", days


def build_arrival_estimates(timetable_data: dict) -> list[dict]:
    """Build nominal estimates without inferring shared trips or stop calls.

    The source heading limits each departure's direction and terminal. The
    diagram span is a distance/time annotation span, not a list of boat calls.
    Its minute labels are reused in both directions as an estimation assumption.
    """
    stop_ids = [stop_data["stop_id"] for stop_data in timetable_data["stops"]]
    stop_positions = {
        stop_id: stop_position
        for stop_position, stop_id in enumerate(stop_ids)
    }
    segment_labels = timetable_data["source_diagram"]["adjacent_segment_labels"]
    segment_duration_by_origin_position = {}
    for segment_label in segment_labels:
        origin_position = stop_positions[segment_label["from_stop_id"]]
        destination_position = stop_positions[segment_label["to_stop_id"]]
        segment_duration_minutes = segment_label["displayed_minutes"]
        if (
            destination_position != origin_position + 1
            or type(segment_duration_minutes) is not int
            or segment_duration_minutes <= 0
        ):
            raise ValueError(f"Invalid adjacent diagram duration: {segment_label}")
        if origin_position in segment_duration_by_origin_position:
            raise ValueError("Duplicate diagram span.")
        segment_duration_by_origin_position[origin_position] = segment_duration_minutes
    if set(segment_duration_by_origin_position) != set(range(len(stop_ids) - 1)):
        raise ValueError("Missing diagram duration; do not invent travel minutes.")

    direct_service_restriction = timetable_data["source_diagram"]["direct_service_restriction"]
    excluded_direct_stop_pair = frozenset(direct_service_restriction["stop_ids"])
    arrival_estimates = []
    for stop_data in timetable_data["stops"]:
        origin_stop_id = stop_data["stop_id"]
        origin_position = stop_positions[origin_stop_id]
        for timetable_column in stop_data["columns"]:
            if timetable_column["event_type"] != "departure":
                continue
            terminal_stop_id = timetable_column["direction_to_stop_id"]
            terminal_position = stop_positions[terminal_stop_id]
            if terminal_position == origin_position:
                raise ValueError("A departure heading must name another terminal.")
            direction_step = 1 if terminal_position > origin_position else -1
            destination_estimates = []
            accumulated_minutes = 0
            diagram_span_stop_ids = [origin_stop_id]
            for stop_position in range(
                origin_position + direction_step,
                terminal_position + direction_step,
                direction_step,
            ):
                accumulated_minutes += segment_duration_by_origin_position[
                    min(stop_position, stop_position - direction_step)
                ]
                destination_stop_id = stop_ids[stop_position]
                diagram_span_stop_ids.append(destination_stop_id)
                # No Oksu <-> Apgujeong direct trip is offered, even though the
                # source has a 9-minute diagram annotation between these stops.
                if (
                    not direct_service_restriction["direct_service_operated"]
                    and frozenset((origin_stop_id, destination_stop_id))
                    == excluded_direct_stop_pair
                ):
                    continue
                destination_estimates.append({
                    "to_stop_id": destination_stop_id,
                    "travel_minutes": accumulated_minutes,
                    "diagram_span_stop_ids": diagram_span_stop_ids.copy(),
                    "stop_call_status": (
                        "requires_confirmation"
                        if destination_stop_id in excluded_direct_stop_pair
                        else "not_certified_by_this_calculation"
                    ),
                })

            for source_row, departure_time in enumerate(
                timetable_column["times_by_source_row"],
                start=1,
            ):
                if departure_time is None:
                    continue
                estimated_arrivals = []
                for destination_estimate in destination_estimates:
                    estimated_arrival_time, arrival_day_offset = calculate_arrival_time(
                        departure_time,
                        destination_estimate["travel_minutes"],
                    )
                    estimated_arrivals.append({
                        **destination_estimate,
                        "estimated_arrival_time": estimated_arrival_time,
                        "arrival_day_offset": arrival_day_offset,
                        "arrival_time_source": "calculated_from_origin_departure_and_diagram_durations",
                    })
                arrival_estimates.append({
                    "source_column_id": timetable_column["column_id"],
                    "source_row": source_row,
                    "from_stop_id": origin_stop_id,
                    "direction_to_stop_id": terminal_stop_id,
                    "departure_time": departure_time,
                    "destinations": estimated_arrivals,
                })
    return arrival_estimates


def build_timetable_data(
    source_image_sha256: str,
) -> tuple[dict, list[dict]]:
    timetable_columns = []
    for (
        stop_id,
        source_heading,
        event_type,
        direction_to_stop_id,
        _source_column_center_x,
        source_row_time_text,
    ) in SOURCE_COLUMNS:
        source_row_times = [
            None if time_token == "-" else time_token
            for time_token in source_row_time_text.split()
        ]
        assert len(source_row_times) == 12
        timetable_columns.append({
            "column_id": build_column_id(stop_id, event_type, direction_to_stop_id),
            "stop_id": stop_id,
            "source_heading": source_heading,
            "event_type": event_type,
            "direction_to_stop_id": direction_to_stop_id,
            "times_by_source_row": source_row_times,
        })

    stops = [
        {
            "stop_id": stop_id,
            "name_ko": stop_name,
            "display_order": stop_position + 1,
            "columns": [
                timetable_column
                for timetable_column in timetable_columns
                if timetable_column["stop_id"] == stop_id
            ],
        }
        for stop_position, (stop_id, stop_name) in enumerate(STOP_NAMES)
    ]
    # stop_id already belongs to each parent stop; avoid duplicate stored facts.
    for stop_data in stops:
        for timetable_column in stop_data["columns"]:
            del timetable_column["stop_id"]

    timetable_data = {
        "schema_version": "1.1.0",
        "dataset_id": "hgbus-timetable-20260815-snapshot-20261103",
        "timezone": "Asia/Seoul",
        "time_format": "HH:mm",
        "source": {
            "title": "한강버스 운항 시간표",
            "page_url": OFFICIAL_SCHEDULE_PAGE_URL,
            "image_url": OFFICIAL_SCHEDULE_IMAGE_URL,
            "image_file": SOURCE_IMAGE_FILE.relative_to(DATA_DIRECTORY).as_posix(),
            "image_sha256": source_image_sha256,
            "image_width": 1500,
            "image_height": 820,
            "retrieved_on": REVIEW_DATE,
        },
        "validity": {
            "effective_from": "2026-08-15",
            "effective_until": None,
            "effective_until_condition": "until_further_notice",
            "operating_days": None,
            "operating_days_status": "not_specified_in_source_image",
            "separate_operational_notices_reviewed": False,
        },
        "representation": {
            "source_row_count": 12,
            "array_position_rule": "times_by_source_row[0] is source table row 1",
            "blank_cell_rule": "null preserves an empty source cell; do not infer its operational meaning",
            "row_alignment_rule": "Source layout only. The same row across columns is not an established trip relationship.",
            "event_type_rule": "Direction headings are departures; columns explicitly headed 도착 are arrivals.",
            "stop_id_authority": "Local identifiers assigned for this dataset; not operator-issued IDs.",
            "trip_linkage_status": "not_established",
        },
        "stops": stops,
        "source_notes": [
            "표기된 시간은 각 선착장 출발 시간 기준입니다.",
            "옥수-압구정간 직항 노선은 미운항 합니다.",
            "적용기간 : 26.8.15.~별도 공지시까지",
        ],
        "source_diagram": {
            "transfer_stop_ids": ["YEOUIDO"],
            "adjacent_segment_labels": [
                {
                    "from_stop_id": current_stop[0],
                    "to_stop_id": next_stop[0],
                    "displayed_minutes": displayed_minutes,
                }
                for current_stop, next_stop, displayed_minutes in zip(
                    STOP_NAMES,
                    STOP_NAMES[1:],
                    [21, 26, 29, 9, 15, 14, 15],
                )
            ],
            "use_rule": "Displayed minutes are used for nominal arrival estimates under arrival_estimation_policy. Diagram spans are not certified stop-call sequences or operational routing edges.",
            "direct_service_restriction": {
                "stop_ids": ["OKSU", "APGUJEONG"],
                "source_note_index": 1,
                "direct_service_operated": False,
            },
        },
        "verification": {
            "status": "source_transcription_and_estimate_calculation_verified",
            "verified_on": REVIEW_DATE,
            "reviewed_by": "assistant_visual_review_with_windows_ocr_comparison",
            "report_file": VALIDATION_REPORT_FILE.name,
            "scope": "Published image transcription and arithmetic of nominal arrival estimates; not actual arrival, operational availability or trip linkage certification.",
        },
    }

    timetable_data["arrival_estimation_policy"] = {
        "method": "origin_departure_plus_diagram_duration_sum",
        "formula": "estimated_arrival = selected_origin_departure + sum(displayed_minutes for diagram spans between origin and destination)",
        "decision_basis": "User-selected estimation method when same-trip linkage cannot be confirmed.",
        "time_base": "Only the selected origin departure; intermediate-stop departure times are not joined or substituted.",
        "dwell_and_wait_minutes_added": False,
        "live_delay_adjustment": False,
        "direction_rule": "Generate only destinations between the origin and the terminal named by its source departure heading.",
        "transfers_included": False,
        "reverse_direction_rule": "Reuse the same displayed minutes in both directions as a project estimation assumption, not a confirmed direction-specific duration.",
        "diagram_span_is_stop_call_sequence": False,
        "nonadjacent_duration_rule": "Sum intervening diagram labels even if a stop is bypassed; this is a nominal estimate, not a published duration for the bypass service.",
        "direct_service_exclusions": [["OKSU", "APGUJEONG"], ["APGUJEONG", "OKSU"]],
        "stop_call_rule": "An estimate does not confirm a stop call for that departure. Calls at Oksu and Apgujeong require separate confirmation.",
        "official_arrival_rule": "Explicit source arrival columns are preserved separately and never overwritten or paired with departures without confirmed trip linkage.",
        "day_offset_rule": "arrival_day_offset is the number of local calendar days after the selected departure's date.",
        "blank_cell_rule": "No arrival estimate is generated from an empty source departure cell.",
    }
    timetable_data["arrival_estimates"] = build_arrival_estimates(timetable_data)
    return timetable_data, timetable_columns


def validate_timetable_structure(
    timetable_data: dict,
    timetable_columns: list[dict],
    source_image_sha256: str,
) -> list[dict]:
    stops = timetable_data["stops"]
    validation_checks = []

    def record_validation_check(
        check_name: str,
        condition: bool,
        details: object = None,
    ) -> None:
        assert condition, f"Validation failed: {check_name}: {details}"
        check_record = {"name": check_name, "status": "passed"}
        if details is not None:
            check_record["details"] = details
        validation_checks.append(check_record)

    stop_ids = [stop_data["stop_id"] for stop_data in stops]
    record_validation_check(
        "stop_count_and_unique_ids",
        len(stop_ids) == len(set(stop_ids)) == 8,
    )
    record_validation_check("source_column_count", len(timetable_columns) == 16)
    record_validation_check(
        "unique_column_ids",
        len({column_data["column_id"] for column_data in timetable_columns}) == 16,
    )
    record_validation_check(
        "source_rows_preserved",
        all(
            len(column_data["times_by_source_row"]) == 12
            for column_data in timetable_columns
        ),
    )
    record_validation_check(
        "direction_ids_reference_known_stops",
        all(
            column_data["direction_to_stop_id"] is None
            or column_data["direction_to_stop_id"] in stop_ids
            for column_data in timetable_columns
        ),
    )
    listed_times = [
        departure_time
        for column_data in timetable_columns
        for departure_time in column_data["times_by_source_row"]
        if departure_time is not None
    ]
    record_validation_check(
        "time_format_and_range",
        all(
            re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", departure_time)
            for departure_time in listed_times
        ),
    )
    record_validation_check(
        "ascending_unique_times_per_column",
        all(
            (
                column_times := [
                    departure_time
                    for departure_time in column_data["times_by_source_row"]
                    if departure_time is not None
                ]
            )
            == sorted(set(column_times))
            for column_data in timetable_columns
        ),
    )
    record_validation_check(
        "effective_date_is_iso_date",
        date.fromisoformat(timetable_data["validity"]["effective_from"])
        == date(2026, 8, 15),
    )
    record_validation_check(
        "explicit_arrival_headers_preserved",
        {
            column_data["column_id"]
            for column_data in timetable_columns
            if column_data["event_type"] == "arrival"
        }
        == {"MAGOK_ARRIVAL", "JAMSIL_ARRIVAL"},
    )
    record_validation_check(
        "unknown_calendar_and_trip_links_not_invented",
        timetable_data["validity"]["operating_days"] is None
        and timetable_data["representation"]["trip_linkage_status"]
        == "not_established",
    )
    record_validation_check(
        "source_image_hash",
        source_image_sha256
        == "55346ad1b6761251cb475c33c9ca9dcdc29e42bfaae897bb454184c306320e82",
    )
    return validation_checks


def validate_arrival_estimates(
    timetable_data: dict,
    stop_ids: list[str],
) -> list[dict]:
    validation_checks = []

    def record_validation_check(
        check_name: str,
        condition: bool,
        details: object = None,
    ) -> None:
        assert condition, f"Validation failed: {check_name}: {details}"
        check_record = {"name": check_name, "status": "passed"}
        if details is not None:
            check_record["details"] = details
        validation_checks.append(check_record)

    arrival_estimates = timetable_data["arrival_estimates"]
    expected_departure_records = {
        (column_data["column_id"], source_row_number): departure_time
        for column_data in timetable_columns
        if column_data["event_type"] == "departure"
        for source_row_number, departure_time in enumerate(
            column_data["times_by_source_row"],
            start=1,
        )
        if departure_time is not None
    }
    record_validation_check(
        "every_source_departure_estimated_once",
        len(arrival_estimates) == len(expected_departure_records)
        and {
            (arrival_record["source_column_id"], arrival_record["source_row"]): (
                arrival_record["departure_time"]
            )
            for arrival_record in arrival_estimates
        }
        == expected_departure_records,
    )
    first_magok_departure = next(
        arrival_record
        for arrival_record in arrival_estimates
        if arrival_record["source_column_id"] == "MAGOK_TO_YEOUIDO"
        and arrival_record["source_row"] == 1
    )
    first_magok_destinations = {
        destination_data["to_stop_id"]: destination_data
        for destination_data in first_magok_departure["destinations"]
    }
    record_validation_check(
        "magok_1120_mangwon_1141_yeouido_1207",
        (
            first_magok_destinations["MANGWON"]["travel_minutes"],
            first_magok_destinations["MANGWON"]["estimated_arrival_time"],
            first_magok_destinations["YEOUIDO"]["travel_minutes"],
            first_magok_destinations["YEOUIDO"]["estimated_arrival_time"],
        )
        == (21, "11:41", 47, "12:07"),
    )
    record_validation_check(
        "no_transfer_beyond_source_direction_terminal",
        all(
            stop_ids.index(arrival_record["from_stop_id"])
            < stop_ids.index(destination_data["to_stop_id"])
            <= stop_ids.index(arrival_record["direction_to_stop_id"])
            if stop_ids.index(arrival_record["from_stop_id"])
            < stop_ids.index(arrival_record["direction_to_stop_id"])
            else stop_ids.index(arrival_record["direction_to_stop_id"])
            <= stop_ids.index(destination_data["to_stop_id"])
            < stop_ids.index(arrival_record["from_stop_id"])
            for arrival_record in arrival_estimates
            for destination_data in arrival_record["destinations"]
        ),
    )
    record_validation_check(
        "no_oksu_apgujeong_direct_estimates",
        all(
            {arrival_record["from_stop_id"], destination_data["to_stop_id"]}
            != {"OKSU", "APGUJEONG"}
            for arrival_record in arrival_estimates
            for destination_data in arrival_record["destinations"]
        ),
    )
    record_validation_check(
        "arrival_estimates_marked_as_calculated",
        all(
            destination_data["arrival_time_source"]
            == "calculated_from_origin_departure_and_diagram_durations"
            for arrival_record in arrival_estimates
            for destination_data in arrival_record["destinations"]
        ),
    )
    record_validation_check(
        "midnight_rollover_retains_day_offset",
        calculate_arrival_time("23:50", 21) == ("00:11", 1),
    )
    return validation_checks


def validate_timetable_data(
    timetable_data: dict,
    timetable_columns: list[dict],
    source_image_sha256: str,
) -> dict:
    validation_checks = validate_timetable_structure(
        timetable_data,
        timetable_columns,
        source_image_sha256,
    )
    stop_ids = [stop_data["stop_id"] for stop_data in timetable_data["stops"]]
    validation_checks.extend(
        validate_arrival_estimates(timetable_data, stop_ids)
    )
    listed_times = [
        departure_time
        for column_data in timetable_columns
        for departure_time in column_data["times_by_source_row"]
        if departure_time is not None
    ]
    ocr_comparison = compare_timetable_with_ocr(timetable_columns, listed_times)
    validation_checks.extend(ocr_comparison["validation_checks"])
    return {
        "validation_checks": validation_checks,
        "ocr_comparison": ocr_comparison,
    }


def compare_timetable_with_ocr(
    timetable_columns: list[dict],
    listed_times: list[str],
) -> dict:
    validation_checks = []

    def record_validation_check(
        check_name: str,
        condition: bool,
        details: object = None,
    ) -> None:
        assert condition, f"Validation failed: {check_name}: {details}"
        check_record = {"name": check_name, "status": "passed"}
        if details is not None:
            check_record["details"] = details
        validation_checks.append(check_record)

    ocr_data = json.loads(
        (RAW_DATA_DIRECTORY / "timetable_ocr_enhanced.json").read_text(
            encoding="utf-8-sig"
        )
    )
    record_validation_check(
        "ocr_image_size",
        (ocr_data["width"], ocr_data["height"]) == (2468, 595),
    )
    # OCR input: source crop (20,357,1472,707), resized from 1452x350 to 2468x595.
    ocr_cells = {}
    for ocr_word in ocr_data["words"]:
        source_x = 20 + (ocr_word["x"] + ocr_word["width"] / 2) * 1452 / 2468
        source_y = 357 + (ocr_word["y"] + ocr_word["height"] / 2) * 350 / 595
        column_index = min(
            range(16),
            key=lambda index: abs(source_x - SOURCE_COLUMNS[index][4]),
        )
        row_index = min(
            range(12),
            key=lambda index: abs(source_y - (376.5 + 28.6 * index)),
        )
        assert abs(source_x - SOURCE_COLUMNS[column_index][4]) < 20
        assert abs(source_y - (376.5 + 28.6 * row_index)) < 10
        assert (column_index, row_index) not in ocr_cells, (
            f"Multiple OCR words in cell {(column_index, row_index)}"
        )
        ocr_cells[column_index, row_index] = ocr_word

    cell_comparisons = []
    normalized_match_count = 0
    visual_resolution_records = []
    blank_cell_count = 0
    for column_index, column_data in enumerate(timetable_columns):
        for row_index, expected_time in enumerate(
            column_data["times_by_source_row"]
        ):
            ocr_word = ocr_cells.get((column_index, row_index))
            normalized_ocr_time = (
                normalize_ocr_time(ocr_word["text"]) if ocr_word else None
            )
            cell_comparison = {
                "column_id": column_data["column_id"],
                "source_row": row_index + 1,
                "json_time": expected_time,
                "ocr_raw": ocr_word["text"] if ocr_word else None,
                "ocr_normalized": normalized_ocr_time,
            }
            if expected_time is None:
                assert ocr_word is None, (
                    f"Unexpected OCR text in source blank: {cell_comparison}"
                )
                cell_comparison["result"] = "blank_preserved"
                blank_cell_count += 1
            else:
                assert ocr_word is not None, (
                    f"OCR missed a transcribed time: {cell_comparison}"
                )
                if normalized_ocr_time == expected_time:
                    cell_comparison["result"] = "ocr_match"
                    normalized_match_count += 1
                elif (
                    column_data["column_id"] == "MAGOK_ARRIVAL"
                    and row_index == 1
                    and expected_time == "13:53"
                    and normalized_ocr_time == "13:33"
                ):
                    cell_comparison["result"] = "visual_match_after_ocr_misread"
                    cell_comparison["review_note"] = (
                        "Original image visibly reads 13:53. Enhanced OCR misread "
                        "the minute tens digit as 3 (13•33). Original-size OCR "
                        "separately read 1353. JSON follows the image."
                    )
                    visual_resolution_records.append(cell_comparison.copy())
                else:
                    raise AssertionError(
                        "Unresolved source/OCR discrepancy: "
                        f"{cell_comparison}"
                    )
            cell_comparisons.append(cell_comparison)

    record_validation_check("source_cell_count", len(cell_comparisons) == 192)
    record_validation_check(
        "nonempty_cells_detected_independently_by_ocr",
        len(ocr_cells) == len(listed_times) == 140,
    )
    record_validation_check("all_52_source_blanks_preserved", blank_cell_count == 52)
    record_validation_check(
        "all_times_compared_with_source",
        normalized_match_count == 139 and len(visual_resolution_records) == 1,
    )
    return {
        "validation_checks": validation_checks,
        "cell_comparisons": cell_comparisons,
        "normalized_match_count": normalized_match_count,
        "visual_resolution_records": visual_resolution_records,
        "blank_cell_count": blank_cell_count,
        "ocr_cell_count": len(ocr_cells),
    }

def save_timetable_data(
    timetable_data: dict,
    validation_checks: list[dict],
) -> str:
    # Keep generated bytes stable across Windows, Linux and Git line endings.
    TIMETABLE_DATA_FILE.write_text(
        json.dumps(timetable_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    reloaded_timetable_data = json.loads(
        TIMETABLE_DATA_FILE.read_text(encoding="utf-8")
    )
    assert reloaded_timetable_data == timetable_data
    validation_checks.append({"name": "saved_json_roundtrip", "status": "passed"})
    return hashlib.sha256(TIMETABLE_DATA_FILE.read_bytes()).hexdigest()


def build_validation_report(
    timetable_data: dict,
    validation_checks: list[dict],
    source_image_sha256: str,
    ocr_comparison: dict,
    dataset_sha256: str,
) -> dict:
    stops = timetable_data["stops"]
    timetable_columns = [
        timetable_column
        for stop_data in stops
        for timetable_column in stop_data["columns"]
    ]
    listed_times = [
        departure_time
        for timetable_column in timetable_columns
        for departure_time in timetable_column["times_by_source_row"]
        if departure_time is not None
    ]
    arrival_estimates = timetable_data["arrival_estimates"]
    cell_comparisons = ocr_comparison["cell_comparisons"]
    normalized_match_count = ocr_comparison["normalized_match_count"]
    visual_resolution_records = ocr_comparison["visual_resolution_records"]
    blank_cell_count = ocr_comparison["blank_cell_count"]
    column_summaries = []
    for stop_data in stops:
        for column_data in stop_data["columns"]:
            column_times = [
                departure_time
                for departure_time in column_data["times_by_source_row"]
                if departure_time is not None
            ]
            column_summaries.append({
                "stop_id": stop_data["stop_id"],
                "stop_name": stop_data["name_ko"],
                "column_id": column_data["column_id"],
                "source_heading": column_data["source_heading"],
                "event_type": column_data["event_type"],
                "time_count": len(column_times),
                "blank_count": 12 - len(column_times),
                "first_listed_time": column_times[0],
                "last_listed_time": column_times[-1],
            })
    departure_time_count = sum(
        1
        for column_data in timetable_columns
        if column_data["event_type"] == "departure"
        for departure_time in column_data["times_by_source_row"]
        if departure_time is not None
    )
    arrival_time_count = len(listed_times) - departure_time_count
    validation_report = {
        "report_version": "1.1.0",
        "status": "passed_for_source_transcription_and_estimate_calculation",
        "review_date": REVIEW_DATE,
        "reviewed_by": "assistant_visual_review_with_windows_ocr_comparison",
        "dataset_file": TIMETABLE_DATA_FILE.name,
        "dataset_sha256": dataset_sha256,
        "source_image_file": SOURCE_IMAGE_FILE.relative_to(
            DATA_DIRECTORY
        ).as_posix(),
        "source_image_sha256": source_image_sha256,
        "source_page_url": OFFICIAL_SCHEDULE_PAGE_URL,
        "source_image_url": OFFICIAL_SCHEDULE_IMAGE_URL,
        "summary": {
            "stops": len(stops),
            "columns": len(timetable_columns),
            "source_rows": 12,
            "source_cells": len(cell_comparisons),
            "time_cells": len(listed_times),
            "departure_time_cells": departure_time_count,
            "arrival_time_cells": arrival_time_count,
            "blank_cells": blank_cell_count,
            "ocr_normalized_matches": normalized_match_count,
            "ocr_misreads_resolved_visually": len(visual_resolution_records),
            "unresolved_transcription_discrepancies": 0,
            "departure_records_with_arrival_estimates": len(arrival_estimates),
            "estimated_origin_destination_records": sum(
                len(arrival_record["destinations"])
                for arrival_record in arrival_estimates
            ),
        },
        "method": {
            "transcription": "Assistant visually transcribed all sixteen columns from the downloaded original image, preserving twelve rows per column.",
            "independent_check": "Windows.Media.Ocr (ko) recognized all 140 nonempty cells in a contrast-enhanced crop. Bounding boxes were mapped back to source columns and rows.",
            "ocr_normalization": "Only punctuation removal and HH:mm colon insertion for exactly four recognized digits; no expected-value-based digit correction.",
            "visual_review": "Original image, headings, blank layout, effective-date footnote and displayed diagram annotations were checked. One enhanced-OCR digit error was resolved against the source and original-size OCR.",
            "reviewer_scope": "Assistant review; not a claim of team or operator approval.",
            "arrival_estimation": timetable_data["arrival_estimation_policy"],
        },
        "structural_checks": validation_checks,
        "column_summary": column_summaries,
        "ocr_visual_resolutions": visual_resolution_records,
        "known_limits": [
            "No operator trip IDs are published in this image. Same-row cells are not automatically linked as a trip.",
            "Empty cells are preserved as null; no additional cancellation, skip-stop or service-calendar meaning is inferred.",
            "Operating weekdays and holiday exceptions are not stated in the image and remain unspecified.",
            "The displayed applicability is 2026-08-15 until further notice. Separate newer operational or cancellation notices have not been audited.",
            "The general departure-time footnote is retained, while the two explicitly headed arrival columns are typed as arrivals.",
            "Arrival estimates are origin departure plus summed diagram durations, as selected by the user. They omit dwell, waits and live delays and may differ from official arrival or intermediate departure times.",
            "The same diagram durations are reused in reverse as an estimation assumption; direction-specific durations have not been confirmed.",
            "Summed diagram spans are not stop-call sequences. Oksu and Apgujeong calls need confirmation; their direct pair is excluded. Nonadjacent estimates use nominal diagram sums, not a published bypass-service duration.",
            "Local stop IDs are dataset identifiers; they are not claimed to be official operator identifiers.",
        ],
        "cell_comparisons": cell_comparisons,
    }

    return validation_report


def save_validation_report(validation_report: dict) -> None:
    VALIDATION_REPORT_FILE.write_text(
        json.dumps(validation_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    assert json.loads(
        VALIDATION_REPORT_FILE.read_text(encoding="utf-8")
    ) == validation_report


def prepare_timetable_data() -> None:
    assert DATA_DIRECTORY.is_dir() and RAW_DATA_DIRECTORY.is_dir(), (
        "Use existing directories only."
    )
    assert SOURCE_IMAGE_FILE.is_file()
    source_image_sha256 = hashlib.sha256(SOURCE_IMAGE_FILE.read_bytes()).hexdigest()
    original_ocr_data = json.loads(
        (RAW_DATA_DIRECTORY / "timetable_ocr.json").read_text(encoding="utf-8-sig")
    )
    assert (original_ocr_data["width"], original_ocr_data["height"]) == (1500, 820)

    timetable_data, timetable_columns = build_timetable_data(source_image_sha256)
    validation_result = validate_timetable_data(
        timetable_data,
        timetable_columns,
        source_image_sha256,
    )
    dataset_sha256 = save_timetable_data(
        timetable_data,
        validation_result["validation_checks"],
    )
    validation_report = build_validation_report(
        timetable_data,
        validation_result["validation_checks"],
        source_image_sha256,
        validation_result["ocr_comparison"],
        dataset_sha256,
    )
    save_validation_report(validation_report)
    print(json.dumps({
        "dataset": str(TIMETABLE_DATA_FILE),
        "report": str(VALIDATION_REPORT_FILE),
        "summary": validation_report["summary"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    prepare_timetable_data()
