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


def test_list_projects_empty(tmp_path):
    with TestClient(create_app(tmp_path / "test.db")) as client:
        response = client.get("/api/projects")
    assert response.status_code == 200
    assert response.json() == []


def test_list_projects_order_and_pagination(tmp_path):
    database_path = tmp_path / "test.db"
    with TestClient(create_app(database_path)) as client:
        with closing(connect(database_path)) as connection:
            with connection:
                connection.executemany(
                    "INSERT INTO projects (id, name, description, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    [
                        ("b", "New B", None, "2026-09-28T10:00:00+00:00"),
                        ("z", "Old", "Earlier project", "2026-09-27T10:00:00+00:00"),
                        ("a", "New A", None, "2026-09-28T10:00:00+00:00"),
                    ],
                )
        response = client.get("/api/projects")
        assert response.status_code == 200
        projects = response.json()
        assert [project["id"] for project in projects] == ["b", "a", "z"]
        assert projects[-1] == {
            "id": "z",
            "name": "Old",
            "description": "Earlier project",
            "created_at": "2026-09-27T10:00:00Z",
        }
        for offset in range(4):
            page = client.get("/api/projects", params={"limit": 1, "offset": offset})
            assert page.status_code == 200
            assert page.json() == projects[offset:offset + 1]


def test_list_projects_default_limit(tmp_path):
    with TestClient(create_app(tmp_path / "test.db")) as client:
        for index in range(21):
            assert client.post("/api/projects", json={"name": str(index)}).status_code == 201
        assert len(client.get("/api/projects").json()) == 20
        assert len(client.get("/api/projects?limit=100").json()) == 21


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "limit=abc",
        "offset=-1",
        "offset=1.5",
        "offset=9223372036854775808",
    ],
)
def test_list_projects_rejects_invalid_pagination(tmp_path, query):
    with TestClient(create_app(tmp_path / "test.db")) as client:
        assert client.get(f"/api/projects?{query}").status_code == 422
