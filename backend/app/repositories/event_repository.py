from datetime import datetime
from uuid import UUID

from psycopg import Connection

from app.models.event import Event


def get_latest_complete_collection(
    connection: Connection,
) -> tuple[UUID, datetime, int] | None:
    result = connection.execute(
        """SELECT collection_run_id, completed_at, saved_record_count
             FROM collection_runs
            WHERE status = 'succeeded'
              AND snapshot_promoted = TRUE
              AND failed_page_count = 0
              AND successful_page_count = expected_page_count
              AND response_list_total_count = saved_record_count
            ORDER BY completed_at DESC
            LIMIT 1"""
    ).fetchone()
    if result is None:
        return None
    return result[0], result[1], result[2]


def count_events(
    connection: Connection,
    collection_run_id: UUID,
    district_name: str | None,
    category: str | None,
) -> int:
    where_clauses = ['collection_run_id = %s']
    parameters: list[object] = [collection_run_id]
    if district_name is not None:
        where_clauses.append('district_name = %s')
        parameters.append(district_name)
    if category is not None:
        where_clauses.append('category = %s')
        parameters.append(category)

    result = connection.execute(
        f"SELECT count(*) FROM events WHERE {' AND '.join(where_clauses)}",
        parameters,
    ).fetchone()
    return int(result[0])


def list_events_by_collection(
    connection: Connection,
    collection_run_id: UUID,
    district_name: str | None,
    category: str | None,
    limit: int,
    offset: int,
) -> tuple[Event, ...]:
    where_clauses = ['collection_run_id = %s']
    parameters: list[object] = [collection_run_id]
    if district_name is not None:
        where_clauses.append('district_name = %s')
        parameters.append(district_name)
    if category is not None:
        where_clauses.append('category = %s')
        parameters.append(category)
    parameters.extend((limit, offset))

    rows = connection.execute(
        f"""SELECT event_id, title, category, district_name, district_code,
                      start_date_text, end_date_text, event_date_text, place,
                      organizer_name, target_audience, fee_text, free_status,
                      inquiry, program_text, organization_url, main_image_url,
                      latitude, longitude, culture_detail_url
                 FROM events
                WHERE {' AND '.join(where_clauses)}
                ORDER BY source_record_number
                LIMIT %s OFFSET %s""",
        parameters,
    ).fetchall()
    return tuple(Event(*row) for row in rows)
