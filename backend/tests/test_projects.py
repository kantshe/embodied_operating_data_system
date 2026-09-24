from contextlib import closing
from datetime import datetime, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.database import connect
from app.main import create_app


@pytest.mark.parametrize("description", [None, "Robot's tabletop experiment"])
def test_create_project_persists(tmp_path, description):
    database_path = tmp_path / "data" / "test.db"
    payload = {"name": "  Pick  "}
    if description is not None:
        payload["description"] = description
    before = datetime.now(timezone.utc)

    with TestClient(create_app(database_path)) as client:
        response = client.post("/api/projects", json=payload)
        assert client.get("/api/health").json() == {"status": "ok"}

    assert response.status_code == 201
    project = response.json()
    assert set(project) == {"id", "name", "description", "created_at"}
    assert UUID(project["id"]).version == 4
    assert project["name"] == "Pick"
    assert project["description"] == description
    created_at = datetime.fromisoformat(project["created_at"].replace("Z", "+00:00"))
    assert before <= created_at <= datetime.now(timezone.utc)

    with closing(connect(database_path)) as connection:
        row = connection.execute(
            "SELECT id, name, description, created_at FROM projects"
        ).fetchone()
    assert row == (project["id"], "Pick", description, created_at.isoformat())


@pytest.mark.parametrize("payload", [{}, {"name": ""}, {"name": " \t\n "}, {"name": None}])
def test_create_project_rejects_invalid_name(tmp_path, payload):
    database_path = tmp_path / "test.db"
    with TestClient(create_app(database_path)) as client:
        response = client.post("/api/projects", json=payload)
    assert response.status_code == 422
    with closing(connect(database_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
