from contextlib import closing
from copy import deepcopy
import json
import hashlib
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from app.database import connect
from app.import_manifest import ImportManifest
from app.main import create_app

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def manifest_for(task_id):
    payload = json.loads((EXAMPLES / "synthetic-manifest.json").read_text())
    payload["episodes"][0]["task_id"] = task_id
    return payload


def counts(database_path):
    with closing(connect(database_path)) as connection:
        return tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("import_batches", "episodes", "assets")
        )


def test_synthetic_example_validates_and_references_real_sample():
    payload = manifest_for(str(uuid4()))
    manifest = ImportManifest.model_validate(payload)
    episode = manifest.episodes[0]
    assert episode.source_type == "synthetic"
    assert episode.started_at.isoformat() == "2026-10-10T01:00:00+00:00"
    assert (EXAMPLES / episode.assets[0].path).is_file()


@pytest.mark.parametrize("path", [
    "/tmp/state.csv", "../state.csv", "a/../state.csv", "a/./state.csv",
    "a//state.csv", "C:/state.csv", r"a\state.csv", "https://a/state.csv",
    "a/\x00state.csv", "a/\nstate.csv", "",
])
def test_manifest_rejects_invalid_paths(path):
    payload = manifest_for(str(uuid4()))
    payload["episodes"][0]["assets"][0]["path"] = path
    with pytest.raises(ValidationError):
        ImportManifest.model_validate(payload)


@pytest.mark.parametrize("changes", [
    {"started_at": "2026-10-10T09:00:00"},
    {"started_at": 1791594000},
    {"started_at": "invalid"},
    {"ended_at": "2026-10-10T09:00:00+08:00"},
    {"ended_at": "2026-10-10T08:59:59+08:00"},
    {"source_type": "real"},
    {"outcome": "unknown"},
    {"device_id": "   "},
    {"task_id": "not-a-uuid"},
    {"assets": []},
    {"unexpected": True},
])
def test_manifest_rejects_invalid_episode(changes):
    payload = manifest_for(str(uuid4()))
    payload["episodes"][0].update(changes)
    with pytest.raises(ValidationError):
        ImportManifest.model_validate(payload)


def test_manifest_rejects_version_unknown_fields_and_duplicates():
    payload = manifest_for(str(uuid4()))
    for changes in (
        {"schema_version": "2.0"}, {"source": " "}, {"episodes": []},
        {"extra": True},
    ):
        with pytest.raises(ValidationError):
            ImportManifest.model_validate({**payload, **changes})
    episode = payload["episodes"][0]
    episode["assets"].append(deepcopy(episode["assets"][0]))
    with pytest.raises(ValidationError):
        ImportManifest.model_validate(payload)
    episode["assets"] = [{"path": "state.json", "asset_type": "state"}]
    with pytest.raises(ValidationError):
        ImportManifest.model_validate(payload)
    episode["assets"] = [{"path": "state.csv", "asset_type": "state", "extra": True}]
    with pytest.raises(ValidationError):
        ImportManifest.model_validate(payload)


def test_import_persists_batch_tasks_and_file_references(tmp_path):
    database_path = tmp_path / "test.db"
    with TestClient(create_app(database_path, source_root=EXAMPLES)) as client:
        project = client.post("/api/projects", json={"name": "Experiment"}).json()
        task_url = f"/api/projects/{project['id']}/tasks"
        tasks = [client.post(task_url, json={"name": name}).json() for name in ("A", "B")]
        payload = manifest_for(tasks[0]["id"])
        second = deepcopy(payload["episodes"][0])
        second.update(id=str(uuid4()), task_id=tasks[1]["id"], source_type="collected")
        payload["episodes"].append(second)
        response = client.post(f"/api/projects/{project['id']}/imports", json=payload)
    assert response.status_code == 201
    result = response.json()
    assert result["project_id"] == project["id"]
    assert result["episode_ids"] == [entry["id"] for entry in payload["episodes"]]
    assert result["asset_count"] == 2
    assert counts(database_path) == (1, 2, 2)
    with closing(connect(database_path)) as connection:
        batch = connection.execute(
            "SELECT project_id, source FROM import_batches WHERE id = ?", (result["id"],)
        ).fetchone()
        assert batch == (project["id"], payload["source"])
        for episode in payload["episodes"]:
            row = connection.execute(
                "SELECT task_id, import_batch_id, started_at, ended_at, source_type "
                "FROM episodes WHERE id = ?", (episode["id"],),
            ).fetchone()
            assert row == (
                episode["task_id"], result["id"], "2026-10-10T01:00:00+00:00",
                "2026-10-10T01:00:02+00:00", episode["source_type"],
            )
        stored = connection.execute(
            "SELECT path, size_bytes, checksum FROM assets"
        ).fetchall()
        content = (EXAMPLES / "episodes/example/state.csv").read_bytes()
        for path, size, checksum in stored:
            assert (tmp_path / "assets" / path).read_bytes() == content
            assert size == len(content)
            assert checksum == hashlib.sha256(content).hexdigest()
        assert json.loads(connection.execute(
            "SELECT results FROM import_results WHERE import_batch_id = ?", (result["id"],)
        ).fetchone()[0]) == result["results"]


@pytest.mark.parametrize("invalid_task", ["missing", "other_project"])
def test_import_reports_task_ownership_per_entry(tmp_path, invalid_task):
    database_path = tmp_path / "test.db"
    with TestClient(create_app(database_path, source_root=EXAMPLES)) as client:
        project = client.post("/api/projects", json={"name": "Experiment"}).json()
        task = client.post(
            f"/api/projects/{project['id']}/tasks", json={"name": "A"}
        ).json()
        if invalid_task == "other_project":
            other = client.post("/api/projects", json={"name": "Other"}).json()
            foreign_id = client.post(
                f"/api/projects/{other['id']}/tasks", json={"name": "B"}
            ).json()["id"]
        else:
            foreign_id = str(uuid4())
        payload = manifest_for(task["id"])
        second = deepcopy(payload["episodes"][0])
        second.update(id=str(uuid4()), task_id=foreign_id)
        payload["episodes"].append(second)
        response = client.post(f"/api/projects/{project['id']}/imports", json=payload)
    assert response.status_code == 201
    assert [item["status"] for item in response.json()["results"]] == ["imported", "failed"]
    assert counts(database_path) == (1, 1, 1)


def test_import_missing_project_invalid_payload_and_conflict_are_atomic(tmp_path):
    database_path = tmp_path / "test.db"
    with TestClient(create_app(database_path, source_root=EXAMPLES)) as client:
        payload = manifest_for(str(uuid4()))
        assert client.post("/api/projects/missing/imports", json=payload).status_code == 404
        project = client.post("/api/projects", json={"name": "Experiment"}).json()
        task = client.post(
            f"/api/projects/{project['id']}/tasks", json={"name": "A"}
        ).json()
        payload = manifest_for(task["id"])
        url = f"/api/projects/{project['id']}/imports"
        invalid = deepcopy(payload)
        invalid["episodes"][0]["assets"][0]["path"] = "../state.csv"
        assert client.post(url, json=invalid).status_code == 422
        assert counts(database_path) == (0, 0, 0)
        assert client.post(url, json=payload).status_code == 201
        fresh = deepcopy(payload["episodes"][0])
        fresh["id"] = str(uuid4())
        payload["episodes"].insert(0, fresh)
        response = client.post(url, json=payload)
        assert response.status_code == 201
        assert [item["status"] for item in response.json()["results"]] == ["imported", "skipped"]
        assert counts(database_path) == (2, 2, 2)
        with closing(connect(database_path)) as connection:
            assert connection.execute(
                "SELECT id FROM episodes WHERE id = ?", (fresh["id"],)
            ).fetchone() is not None
        retry = client.post(url, json=payload).json()
        assert [item["status"] for item in retry["results"]] == ["skipped", "skipped"]
        assert retry["asset_count"] == 0
        assert retry["episode_ids"] == []
        assert counts(database_path) == (3, 2, 2)


def test_import_rolls_back_on_database_insert_failure(tmp_path):
    database_path = tmp_path / "test.db"
    with TestClient(create_app(database_path, source_root=EXAMPLES)) as client:
        project = client.post("/api/projects", json={"name": "Experiment"}).json()
        task = client.post(
            f"/api/projects/{project['id']}/tasks", json={"name": "A"}
        ).json()
        with closing(connect(database_path)) as connection:
            connection.executescript(
                "CREATE TRIGGER fail_asset BEFORE INSERT ON assets "
                "BEGIN SELECT RAISE(ABORT, 'Simulated insert failure'); END;"
            )
        response = client.post(
            f"/api/projects/{project['id']}/imports", json=manifest_for(task["id"])
        )
    assert response.status_code == 409
    assert counts(database_path) == (0, 0, 0)
    assert list((tmp_path / "assets").iterdir()) == []


@pytest.mark.parametrize("failure", ["missing", "directory", "symlink", "partial", "parent_symlink"])
def test_failed_files_are_cleaned_and_valid_entries_continue(tmp_path, failure):
    source = tmp_path / "incoming"
    source.mkdir()
    (source / "good.csv").write_bytes(b"timestamp_s,value\n0,1\n")
    if failure == "directory":
        (source / "bad.csv").mkdir()
    elif failure == "symlink":
        outside = tmp_path / "outside.csv"
        outside.write_bytes(b"outside")
        (source / "bad.csv").symlink_to(outside)
    elif failure == "parent_symlink":
        (tmp_path / "bad.csv").write_bytes(b"outside")
        (source / "link").symlink_to(tmp_path, target_is_directory=True)
    database_path = tmp_path / "test.db"
    with TestClient(create_app(database_path)) as client:
        project = client.post("/api/projects", json={"name": "A"}).json()
        task = client.post(
            f"/api/projects/{project['id']}/tasks", json={"name": "A"}
        ).json()
        payload = manifest_for(task["id"])
        bad = payload["episodes"][0]
        bad["assets"] = [{"path": "bad.csv", "asset_type": "state"}]
        if failure == "parent_symlink":
            bad["assets"][0]["path"] = "link/bad.csv"
        if failure == "partial":
            bad["assets"].insert(0, {"path": "good.csv", "asset_type": "state"})
        good = deepcopy(bad)
        good["id"] = str(uuid4())
        good["assets"] = [{"path": "good.csv", "asset_type": "state"}]
        payload["episodes"].append(good)
        result = client.post(f"/api/projects/{project['id']}/imports", json=payload).json()
    assert [item["status"] for item in result["results"]] == ["failed", "imported"]
    assert result["episode_ids"] == [good["id"]]
    assert counts(database_path) == (1, 1, 1)
    assert not (tmp_path / "assets" / result["id"] / "0").exists()
    assert len(list((tmp_path / "assets").rglob("*.csv"))) == 1
    assert (source / "good.csv").read_bytes() == b"timestamp_s,value\n0,1\n"


def test_duplicates_within_manifest_and_other_project_do_not_overwrite(tmp_path):
    database_path = tmp_path / "test.db"
    with TestClient(create_app(database_path, source_root=EXAMPLES)) as client:
        projects = [client.post("/api/projects", json={"name": name}).json() for name in ("A", "B")]
        tasks = [client.post(
            f"/api/projects/{project['id']}/tasks", json={"name": "Pick"}
        ).json() for project in projects]
        payload = manifest_for(tasks[0]["id"])
        payload["episodes"] *= 2
        result = client.post(f"/api/projects/{projects[0]['id']}/imports", json=payload).json()
        assert [item["status"] for item in result["results"]] == ["imported", "skipped"]
        payload = manifest_for(tasks[1]["id"])
        result = client.post(f"/api/projects/{projects[1]['id']}/imports", json=payload).json()
        assert result["results"][0]["status"] == "failed"
        assert counts(database_path) == (2, 1, 1)


def test_results_table_added_to_existing_database(tmp_path):
    from app.database import initialize_database
    database_path = tmp_path / "test.db"
    initialize_database(database_path)
    with closing(connect(database_path)) as connection:
        connection.execute("DROP TABLE import_results")
        connection.execute(
            "INSERT INTO projects VALUES ('legacy', 'Legacy', NULL, '2026-10-10')"
        )
        connection.commit()
    with TestClient(create_app(database_path)) as client:
        assert client.get("/api/projects/legacy").status_code == 200
    with closing(connect(database_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_results").fetchone()[0] == 0
