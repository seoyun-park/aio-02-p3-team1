from fastapi.testclient import TestClient

from app.main import app


def test_health_reports_server_is_running():
    response = TestClient(app).get('/health')

    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


def test_local_frontend_can_read_health():
    response = TestClient(app).get('/health', headers={'Origin': 'http://localhost:5173'})

    assert response.headers['access-control-allow-origin'] == 'http://localhost:5173'
