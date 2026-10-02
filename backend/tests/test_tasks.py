from contextlib import closing
from datetime import datetime, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.database import connect
from app.main import create_app


@pytest.mark.parametrize(
    "optional", [{}, {"objective": "Pick robot's block", "scene": "Tabletop"}]
)
def test_create_task_persists(tmp_path, optional):
    database_path = tmp_path / "test.db"
    before = datetime.now(timezone.utc)
    with TestClient(create_app(database_path)) as client:
        project = client.post("/api/projects", json={"name": "Experiment"}).json()
        response = client.post(
            f"/api/projects/{project['id']}/tasks",
            json={"name": "  Pick  ", **optional},
        )
    assert response.status_code == 201
    task = response.json()
    assert set(task) == {"id", "project_id", "name", "objective", "scene", "created_at"}
    assert UUID(task["id"]).version == 4
    assert task["project_id"] == project["id"]
    assert task["name"] == "Pick"
    assert task["objective"] == optional.get("objective")
    assert task["scene"] == optional.get("scene")
    created_at = datetime.fromisoformat(task["created_at"].replace("Z", "+00:00"))
    assert before <= created_at <= datetime.now(timezone.utc)
    with closing(connect(database_path)) as connection:
        row = connection.execute(
            "SELECT id, project_id, name, objective, scene, created_at FROM tasks"
        ).fetchone()
    assert row == (
        task["id"], project["id"], "Pick",
        optional.get("objective"), optional.get("scene"), created_at.isoformat(),
    )


@pytest.mark.parametrize("payload", [{}, {"name": ""}, {"name": " \t\n "}, {"name": None}])
def test_create_task_rejects_invalid_name(tmp_path, payload):
    database_path = tmp_path / "test.db"
    with TestClient(create_app(database_path)) as client:
        project = client.post("/api/projects", json={"name": "Experiment"}).json()
        response = client.post(f"/api/projects/{project['id']}/tasks", json=payload)
    assert response.status_code == 422
    with closing(connect(database_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_create_task_requires_existing_project(tmp_path):
    database_path = tmp_path / "test.db"
    with TestClient(create_app(database_path)) as client:
        client.post("/api/projects", json={"name": "Other"})
        response = client.post("/api/projects/missing/tasks", json={"name": "Pick"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Project not found"}
    with closing(connect(database_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_list_tasks_empty_and_missing_project(tmp_path):
    with TestClient(create_app(tmp_path / "test.db")) as client:
        project = client.post("/api/projects", json={"name": "Experiment"}).json()
        response = client.get(f"/api/projects/{project['id']}/tasks")
        assert response.status_code == 200
        assert response.json() == []
        missing = client.get("/api/projects/missing/tasks")
        assert missing.status_code == 404
        assert missing.json() == {"detail": "Project not found"}


def test_list_tasks_filters_orders_and_paginates(tmp_path):
    database_path = tmp_path / "test.db"
    with TestClient(create_app(database_path)) as client:
        project = client.post("/api/projects", json={"name": "Experiment"}).json()
        other = client.post("/api/projects", json={"name": "Other"}).json()
        url = f"/api/projects/{project['id']}/tasks"
        with closing(connect(database_path)) as connection:
            with connection:
                connection.executemany(
                    "INSERT INTO tasks (id, project_id, name, objective, scene, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        ("z", project["id"], "Old", None, None, "2026-10-02T00:00:00+00:00"),
                        ("a", project["id"], "New A", None, None, "2026-10-03T00:00:00+00:00"),
                        ("b", project["id"], "New B", "Pick", "Table", "2026-10-03T00:00:00+00:00"),
                        ("x", other["id"], "Other", None, None, "2026-10-04T00:00:00+00:00"),
                    ],
                )
        response = client.get(url)
        assert response.status_code == 200
        tasks = response.json()
        assert [task["id"] for task in tasks] == ["b", "a", "z"]
        assert tasks[0] == {
            "id": "b", "project_id": project["id"], "name": "New B",
            "objective": "Pick", "scene": "Table", "created_at": "2026-10-03T00:00:00Z",
        }
        for offset in range(4):
            page = client.get(url, params={"limit": 1, "offset": offset})
            assert page.status_code == 200
            assert page.json() == tasks[offset:offset + 1]


def test_list_tasks_default_limit(tmp_path):
    with TestClient(create_app(tmp_path / "test.db")) as client:
        project = client.post("/api/projects", json={"name": "Experiment"}).json()
        url = f"/api/projects/{project['id']}/tasks"
        for index in range(21):
            assert client.post(url, json={"name": str(index)}).status_code == 201
        assert len(client.get(url).json()) == 20
        assert len(client.get(url, params={"limit": 100}).json()) == 21


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=101", "limit=abc", "offset=-1", "offset=1.5", "offset=9223372036854775808"],
)
def test_list_tasks_rejects_invalid_pagination(tmp_path, query):
    with TestClient(create_app(tmp_path / "test.db")) as client:
        project = client.post("/api/projects", json={"name": "Experiment"}).json()
        assert client.get(f"/api/projects/{project['id']}/tasks?{query}").status_code == 422
