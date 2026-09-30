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
