from fastapi import APIRouter, Depends
from psycopg import Connection

from app.core.database import get_database_connection


router = APIRouter(tags=['health'])


@router.get('/ready')
def get_readiness_status(
    connection: Connection = Depends(get_database_connection),
) -> dict[str, object]:
    connection.execute('SELECT 1')
    return {'status': 'ready', 'checks': {'database': 'ok'}}
