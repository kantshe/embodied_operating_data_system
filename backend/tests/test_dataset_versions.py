from contextlib import closing
import hashlib

from fastapi.testclient import TestClient
import pytest

from app.database import connect
from app.main import create_app
from test_episode_detail import record


@pytest.fixture
def version_record(record):
    client, url, database, storage, other = record
    with closing(connect(database)) as connection:
        with connection:
            for row in connection.execute("SELECT id,path FROM assets").fetchall():
                data = (storage / row[1]).read_bytes()
                connection.execute("UPDATE assets SET size_bytes=?,checksum=? WHERE id=?", (len(data), hashlib.sha256(data).hexdigest(), row[0]))
    return client, url.rsplit("/episodes/", 1)[0] + "/versions", url, database, storage, other


def create(client, url, **kwargs):
    return client.post(url, json={"name": "  baseline  ", "episode_ids": ["episode"], **kwargs})


def test_snapshot_is_frozen_and_survives_restart(version_record):
    client, url, episode_url, database, storage, _ = version_record
    report = client.post(episode_url + "/quality-check").json()
    response = create(client, url)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "baseline" and data["episode_count"] == 1
    snapshot = data["snapshot"]
    assert snapshot["schema_version"] == 1
    episode = snapshot["episodes"][0]
    assert episode["task"]["name"] == "Pick"
    assert episode["quality_report"]["id"] == report["id"]
    assert episode["quality_report"]["issues"] == []
    assert episode["assets"][0]["checksum"] == hashlib.sha256((storage / "batch/state.csv").read_bytes()).hexdigest()
    assert client.patch(episode_url + "/annotation", json={"outcome": "failure", "failure_reason": "missed", "expected_version": 0}).status_code == 200
    client.post(episode_url + "/quality-check")
    with closing(connect(database)) as connection:
        with connection:
            connection.execute("UPDATE tasks SET name='Changed'")
            connection.execute("UPDATE projects SET name='Changed'")
    (storage / "batch/state.csv").unlink()
    assert client.get(url + "/" + data["id"]).json() == data
    with TestClient(create_app(database)) as restarted:
        assert restarted.get(url + "/" + data["id"]).json() == data
    listing = client.get(url).json()
    assert listing == [{key: value for key, value in data.items() if key != "snapshot"}]


def test_no_report_and_missing_legacy_checksums(version_record):
    client, url, _, database, storage, _ = version_record
    with closing(connect(database)) as connection:
        with connection:
            connection.execute("UPDATE assets SET checksum=NULL,size_bytes=NULL")
    data = create(client, url).json()
    episode = data["snapshot"]["episodes"][0]
    assert episode["quality_report"] is None
    for asset in episode["assets"]:
        raw = (storage / asset["path"]).read_bytes()
        assert asset["checksum"] == hashlib.sha256(raw).hexdigest()
        assert asset["size_bytes"] == len(raw)
    with closing(connect(database)) as connection:
        assert connection.execute("SELECT checksum FROM assets").fetchall() == [(None,), (None,)]


@pytest.mark.parametrize("payload", [
    {"name": " "}, {"name": "x" * 201}, {"episode_ids": []},
    {"episode_ids": ["episode", "episode"]}, {"episode_ids": [""]},
    {"episode_ids": [str(i) for i in range(501)]}, {"episode_ids": [1]},
    {"extra": True},
])
def test_invalid_request(version_record, payload):
    client, url, *_ = version_record
    assert create(client, url, **payload).status_code == 422
    assert client.get(url).json() == []


def test_project_isolation_and_atomic_selection(version_record):
    client, url, _, _, _, other = version_record
    foreign = f"/api/projects/{other['id']}/versions"
    data = create(client, url).json()
    assert create(client, foreign).status_code == 404
    assert client.get(foreign).json() == []
    assert client.get(foreign + "/" + data["id"]).status_code == 404
    assert create(client, url, episode_ids=["episode", "absent"]).status_code == 404
    assert len(client.get(url).json()) == 1
    assert client.get(url + "/absent").status_code == 404
    assert client.get("/api/projects/absent/versions").status_code == 404


@pytest.mark.parametrize("kind", ["missing", "changed", "symlink", "no_assets", "wrong_size", "directory", "invalid_path"])
def test_bad_assets_abort_version(version_record, kind):
    client, url, _, database, storage, _ = version_record
    path = storage / "batch/state.csv"
    if kind == "missing":
        path.unlink()
    elif kind == "changed":
        path.write_bytes(b"changed")
    elif kind == "symlink":
        path.unlink()
        path.symlink_to(storage / "batch/video.mp4")
    elif kind == "directory":
        path.unlink()
        path.mkdir()
    else:
        with closing(connect(database)) as connection:
            with connection:
                connection.execute({"no_assets": "DELETE FROM assets", "wrong_size": "UPDATE assets SET size_bytes=999", "invalid_path": "UPDATE assets SET path='../outside'"}[kind])
    assert create(client, url).status_code == 409
    assert client.get(url).json() == []


def test_pagination_order_and_selected_order(version_record):
    client, url, _, database, *_ = version_record
    with closing(connect(database)) as connection:
        with connection:
            connection.execute("INSERT INTO episodes (id,task_id,created_at) SELECT 'second',task_id,created_at FROM episodes")
            connection.execute("INSERT INTO assets (id,episode_id,path,asset_type) VALUES ('second','second','batch/state.csv','state')")
    first = create(client, url, episode_ids=["second", "episode"]).json()
    assert [item["id"] for item in first["snapshot"]["episodes"]] == ["second", "episode"]
    second = create(client, url).json()
    assert client.get(url + "?limit=1").json()[0]["id"] == second["id"]
    assert client.get(url + "?limit=1&offset=1").json()[0]["id"] == first["id"]
    assert client.get(url + "?offset=2").json() == []
    for query in ("limit=0", "limit=101", "offset=-1", "offset=9223372036854775808"):
        assert client.get(url + "?" + query).status_code == 422


def test_insert_failure_leaves_no_version(version_record):
    _, url, _, database, *_ = version_record
    with closing(connect(database)) as connection:
        connection.execute("CREATE TRIGGER reject_version BEFORE INSERT ON dataset_versions BEGIN SELECT RAISE(ABORT, 'test'); END")
    with TestClient(create_app(database), raise_server_exceptions=False) as client:
        assert create(client, url).status_code == 500
        assert client.get(url).json() == []


def test_latest_failed_report_is_preserved(version_record):
    client, url, episode_url, database, *_ = version_record
    client.post(episode_url + "/quality-check")
    with closing(connect(database)) as connection:
        with connection:
            connection.execute("UPDATE episodes SET device_id=NULL")
    latest = client.post(episode_url + "/quality-check").json()
    assert not latest["passed"]
    response = create(client, url)
    assert response.status_code == 201
    stored = response.json()["snapshot"]["episodes"][0]["quality_report"]
    assert stored["id"] == latest["id"] and stored["issues"] == latest["issues"]


def test_file_mutation_during_read_aborts_snapshot(version_record, monkeypatch):
    from app import dataset_versions
    client, url, _, _, storage, _ = version_record
    original = dataset_versions.open_asset

    class ChangingFile:
        def __init__(self, stream):
            self.stream = stream
            self.changed = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            data = self.stream.read(size)
            if not self.changed:
                self.changed = True
                with (storage / "batch/state.csv").open("ab") as output:
                    output.write(b"2,3\n")
            return data

    monkeypatch.setattr(dataset_versions, "open_asset", lambda root, path: ChangingFile(original(root, path)))
    assert create(client, url).status_code == 409
    assert client.get(url).json() == []
