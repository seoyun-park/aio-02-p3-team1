from psycopg import Connection

from app.models.place import Place


PLACE_COLUMNS = """place_id, name, category, road_address, lot_address, district,
    latitude, longitude, phone, website, managing_org, description, facilities,
    opening_hours, closed_days, admission_info, subtype, source_count,
    primary_source_id, source_url, last_collected_at, last_verified_at, is_active"""


def build_place_filters(
    category: str | None,
    district: str | None,
) -> tuple[list[str], list[object]]:
    where_clauses = ['is_active = TRUE']
    parameters: list[object] = []
    if category is not None:
        where_clauses.append('category = %s')
        parameters.append(category)
    if district is not None:
        where_clauses.append('district = %s')
        parameters.append(district)
    return where_clauses, parameters


def count_active_places(
    connection: Connection,
    category: str | None,
    district: str | None,
) -> int:
    where_clauses, parameters = build_place_filters(category, district)
    result = connection.execute(
        f"SELECT count(*) FROM places WHERE {' AND '.join(where_clauses)}",
        parameters,
    ).fetchone()
    return int(result[0])


def list_active_places(
    connection: Connection,
    category: str | None,
    district: str | None,
    limit: int,
    offset: int,
) -> tuple[Place, ...]:
    where_clauses, parameters = build_place_filters(category, district)
    parameters.extend((limit, offset))
    rows = connection.execute(
        f"""SELECT {PLACE_COLUMNS}
              FROM places
             WHERE {' AND '.join(where_clauses)}
             ORDER BY name, place_id
             LIMIT %s OFFSET %s""",
        parameters,
    ).fetchall()
    return tuple(Place(*row) for row in rows)


def list_active_recommendation_places(
    connection: Connection,
    categories: tuple[str, ...],
) -> tuple[Place, ...]:
    if not categories:
        return ()
    rows = connection.execute(
        f"""SELECT {PLACE_COLUMNS}
              FROM places
             WHERE is_active = TRUE
               AND category = ANY(%s)
               AND latitude IS NOT NULL
               AND longitude IS NOT NULL
             ORDER BY name, place_id""",
        (list(categories),),
    ).fetchall()
    return tuple(Place(*row) for row in rows)


def get_active_place(connection: Connection, place_id: str) -> Place | None:
    row = connection.execute(
        f"""SELECT {PLACE_COLUMNS}
              FROM places
             WHERE place_id = %s AND is_active = TRUE""",
        (place_id,),
    ).fetchone()
    return Place(*row) if row is not None else None
