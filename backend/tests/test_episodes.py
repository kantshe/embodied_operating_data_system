from contextlib import closing

from fastapi.testclient import TestClient
import pytest

from app.database import connect
from app.main import create_app


@pytest.fixture
def seeded(tmp_path):
    database_path = tmp_path / "test.db"
    with TestClient(create_app(database_path)) as client:
        projects = [client.post("/api/projects", json={"name": name}).json() for name in ("A", "B")]
        tasks = [
            client.post(f"/api/projects/{project['id']}/tasks", json={"name": f"Task {index}"}).json()
            for index, project in enumerate((projects[0], projects[0], projects[1]))
        ]
        with closing(connect(database_path)) as connection:
            with connection:
                connection.executemany(
                    "INSERT INTO episodes (id, task_id, device_id, started_at, ended_at, "
                    "outcome, source_type, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        ("a", tasks[0]["id"], "arm-a", "2026-10-13T09:00:00+08:00", None, "success", "synthetic", "2026-10-13T01:00:00Z"),
                        ("b", tasks[1]["id"], "arm-b", "2026-10-13T02:00:00Z", None, "failure", "collected", "2026-10-13T02:00:00+00:00"),
                        ("c", tasks[0]["id"], "arm-a", None, None, "pending", "synthetic", "2026-10-13T02:00:00Z"),
                        ("foreign", tasks[2]["id"], "arm-a", "2026-10-13T01:00:00Z", None, "success", "synthetic", "2026-10-13T03:00:00Z"),
                    ],
                )
        yield client, projects, tasks, database_path


def test_list_isolates_orders_and_paginates(seeded):
    client, projects, tasks, _ = seeded
    url = f"/api/projects/{projects[0]['id']}/episodes"
    response = client.get(url)
    assert response.status_code == 200
    rows = response.json()
    assert [row["id"] for row in rows] == ["c", "b", "a"]
    assert rows[0]["task_name"] == "Task 0"
    assert rows[0]["started_at"] is None
    assert rows[0]["import_batch_id"] is None
    for offset in range(4):
        assert client.get(url, params={"limit": 1, "offset": offset}).json() == rows[offset:offset + 1]
    assert client.get(url, params={"task_id": tasks[2]["id"]}).json() == []
    assert client.get("/api/projects/missing/episodes").status_code == 404
    empty = client.post("/api/projects", json={"name": "Empty"}).json()
    assert client.get(f"/api/projects/{empty['id']}/episodes").json() == []


def test_filters_exact_matching_timezone_and_inclusive_boundaries(seeded):
    client, projects, tasks, _ = seeded
    url = f"/api/projects/{projects[0]['id']}/episodes"
    assert [row["id"] for row in client.get(url, params={"device_id": "arm-a"}).json()] == ["c", "a"]
    assert client.get(url, params={"device_id": "arm"}).json() == []
    assert client.get(url, params={"device_id": "' OR 1=1 --"}).json() == []
    assert [row["id"] for row in client.get(url, params={"outcome": "failure"}).json()] == ["b"]
    response = client.get(url, params={
        "task_id": tasks[0]["id"], "device_id": "arm-a", "outcome": "success",
        "started_from": "2026-10-13T01:00:00Z", "started_to": "2026-10-13T09:00:00+08:00",
    })
    assert [row["id"] for row in response.json()] == ["a"]
    assert [row["id"] for row in client.get(url, params={"started_from": "2026-10-13T02:00:00Z"}).json()] == ["b"]
    assert [row["id"] for row in client.get(url, params={"started_to": "2026-10-13T01:00:00Z"}).json()] == ["a"]


@pytest.mark.parametrize("query", [
    {"limit": 0}, {"limit": 101}, {"offset": -1}, {"offset": 9223372036854775808},
    {"outcome": "unknown"}, {"started_from": "invalid"},
    {"started_from": "2026-10-13T01:00:00"},
    {"started_from": "2026-10-14T00:00:00Z", "started_to": "2026-10-13T00:00:00Z"},
])
def test_invalid_filters(seeded, query):
    client, projects, _, _ = seeded
    assert client.get(f"/api/projects/{projects[0]['id']}/episodes", params=query).status_code == 422


def test_default_limit(seeded):
    client, projects, tasks, database_path = seeded
    with closing(connect(database_path)) as connection:
        with connection:
            connection.executemany(
                "INSERT INTO episodes (id, task_id, created_at) VALUES (?, ?, ?)",
                [(f"extra-{index}", tasks[0]["id"], "2026-10-13T04:00:00Z") for index in range(21)],
            )
    url = f"/api/projects/{projects[0]['id']}/episodes"
    assert len(client.get(url).json()) == 20
    assert len(client.get(url, params={"limit": 100}).json()) == 24
