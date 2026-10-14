from contextlib import closing
import io
import os

from fastapi.testclient import TestClient
import pytest

from app.database import connect
from app.episode_detail import SUMMARY_BYTES, SUMMARY_ROWS, state_summary
from app.main import create_app


@pytest.fixture
def record(tmp_path):
    database = tmp_path / "test.db"
    storage = tmp_path / "assets"
    (storage / "batch").mkdir(parents=True)
    (storage / "batch/state.csv").write_bytes(b"timestamp_s,joint_1_rad\n0,0.1\n1,0.2\n")
    (storage / "batch/video.mp4").write_bytes(b"0123456789")
    with TestClient(create_app(database)) as client:
        project = client.post("/api/projects", json={"name": "A"}).json()
        other = client.post("/api/projects", json={"name": "B"}).json()
        task = client.post(f"/api/projects/{project['id']}/tasks", json={"name": "Pick"}).json()
        with closing(connect(database)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO episodes "
                    "(id,task_id,device_id,started_at,ended_at,outcome,source_type,created_at) "
                    "VALUES ('episode',?,'arm','2026-10-14T01:00:00Z',"
                    "'2026-10-14T01:00:01Z','success','synthetic','2026-10-14T02:00:00Z')",
                    (task["id"],),
                )
                connection.executemany(
                    "INSERT INTO assets (id,episode_id,path,asset_type,size_bytes,checksum) "
                    "VALUES (?,'episode',?,?,?,?)",
                    [
                        ("state", "batch/state.csv", "state", 41, "recorded-checksum"),
                        ("video", "batch/video.mp4", "video", 10, None),
                    ],
                )
        yield client, f"/api/projects/{project['id']}/episodes/episode", database, storage, other


def test_detail_and_fresh_basic_check(record):
    client, url, database, storage, _ = record
    response = client.get(url)
    assert response.status_code == 200
    detail = response.json()
    assert detail["id"] == "episode"
    assert detail["task_name"] == "Pick"
    state, video = detail["assets"]
    assert state["status"] == video["status"] == "available"
    assert state["actual_size_bytes"] == (storage / "batch/state.csv").stat().st_size
    assert state["checksum"] == "recorded-checksum"
    assert state["state_summary"] == {
        "status": "ready", "columns": ["timestamp_s", "joint_1_rad"], "row_count": 2,
        "preview": [["0", "0.1"], ["1", "0.2"]], "truncated": False,
    }
    assert video["state_summary"] is None
    check = client.post(url + "/quality-check").json()
    assert check["passed"] is True
    assert check["issues"] == []
    # This step returns a fresh inspection; report history is a later feature.
    with closing(connect(database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM quality_reports").fetchone()[0] == 0
    (storage / "batch/state.csv").unlink()
    assert client.get(url).json()["assets"][0]["status"] == "missing"
    check = client.post(url + "/quality-check").json()
    assert check["passed"] is False
    assert {"code": "file_missing", "field": "path", "asset_id": "state"} in check["issues"]
    assert client.get(url + "/assets/state/content").status_code == 404


def test_episode_and_asset_ownership(record):
    client, url, _, _, other = record
    foreign = f"/api/projects/{other['id']}/episodes/episode"
    for suffix in ("", "/assets/state/content"):
        assert client.get(foreign + suffix).status_code == 404
        assert client.get(url.rsplit("/", 1)[0] + "/absent" + suffix).status_code == 404
    assert client.post(foreign + "/quality-check").status_code == 404
    assert client.get(url + "/assets/absent/content").status_code == 404
    # An asset in the same project but a different episode is also inaccessible.
    with closing(connect(record[2])) as connection:
        with connection:
            connection.execute(
                "INSERT INTO episodes (id,task_id,created_at) "
                "SELECT 'second',task_id,created_at FROM episodes WHERE id='episode'"
            )
    assert client.get(url.rsplit("/", 1)[0] + "/second/assets/state/content").status_code == 404


@pytest.mark.parametrize("range_value,expected,status,content_range", [
    (None, b"0123456789", 200, None),
    ("bytes=2-5", b"2345", 206, "bytes 2-5/10"),
    ("bytes=7-", b"789", 206, "bytes 7-9/10"),
    ("bytes=-3", b"789", 206, "bytes 7-9/10"),
    ("bytes=8-99", b"89", 206, "bytes 8-9/10"),
    ("bytes=-99", b"0123456789", 206, "bytes 0-9/10"),
])
def test_file_content_and_ranges(record, range_value, expected, status, content_range):
    client, url, *_ = record
    response = client.get(url + "/assets/video/content", headers={"Range": range_value} if range_value else {})
    assert response.status_code == status
    assert response.content == expected
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers.get("content-range") == content_range
    assert int(response.headers["content-length"]) == len(expected)
    assert response.headers["accept-ranges"] == "bytes"


@pytest.mark.parametrize("value", [
    "bytes=10-", "bytes=7-2", "bytes=-0", "bytes=-", "bytes=0-1,4-5",
    "items=1-2", "bytes=" + "9" * 5000 + "-",
])
def test_invalid_ranges(record, value):
    client, url, *_ = record
    response = client.get(url + "/assets/video/content", headers={"Range": value})
    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */10"


def test_required_fields_no_assets_and_invalid_values(record):
    client, url, database, *_ = record
    with closing(connect(database)) as connection:
        with connection:
            connection.execute(
                "UPDATE episodes SET device_id=' ',started_at=NULL,ended_at=NULL,"
                "outcome='unknown',source_type='' WHERE id='episode'"
            )
            connection.execute("DELETE FROM assets")
    # Partial historical records remain inspectable rather than causing a 500.
    assert client.get(url).status_code == 200
    check = client.post(url + "/quality-check").json()
    codes = {(issue["code"], issue["field"]) for issue in check["issues"]}
    assert {("required_field", name) for name in ("device_id", "started_at", "ended_at", "source_type")} <= codes
    assert ("required_assets", "assets") in codes
    assert ("invalid_value", "outcome") in codes


@pytest.mark.parametrize("path", ["../outside.csv", "/outside.csv", "a//b.csv", r"a\b.csv", "file:bad.csv"])
def test_unsafe_paths_never_read_or_stream(record, path):
    client, url, database, *_ = record
    with closing(connect(database)) as connection:
        with connection:
            connection.execute("UPDATE assets SET path=? WHERE id='state'", (path,))
    assert client.get(url).json()["assets"][0]["status"] == "invalid_path"
    assert client.get(url + "/assets/state/content").status_code == 404
    assert any(issue["code"] == "file_invalid_path" for issue in client.post(url + "/quality-check").json()["issues"])


@pytest.mark.parametrize("kind", ["symlink", "parent_symlink", "directory", "fifo"])
def test_non_regular_files_are_rejected(record, kind):
    client, url, database, storage, _ = record
    path = storage / "batch/state.csv"
    path.unlink()
    if kind == "symlink":
        outside = storage.parent / "outside.csv"
        outside.write_text("private")
        path.symlink_to(outside)
    elif kind == "parent_symlink":
        (storage / "link").symlink_to(storage / "batch", target_is_directory=True)
        (storage / "batch/state.csv").write_text("data")
        with closing(connect(database)) as connection:
            with connection:
                connection.execute("UPDATE assets SET path='link/state.csv' WHERE id='state'")
    elif kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    assert client.get(url).json()["assets"][0]["status"] == "invalid_path"
    assert client.get(url + "/assets/state/content").status_code == 404


def test_unreadable_and_empty_files(record, monkeypatch):
    client, url, _, storage, _ = record
    (storage / "batch/state.csv").write_bytes(b"")
    assert any(issue["code"] == "empty_file" for issue in client.post(url + "/quality-check").json()["issues"])
    from app import episode_detail
    monkeypatch.setattr(episode_detail, "open_asset", lambda *args: (_ for _ in ()).throw(PermissionError()))
    assert client.get(url).json()["assets"][0]["status"] == "unreadable"
    assert client.get(url + "/assets/state/content").status_code == 404


@pytest.mark.parametrize("data", [
    b"\xff", b"", b"a,a\n1,2\n", b"a,\n1,2\n", b"a,b\n1\n", b'a,b\n"unclosed',
])
def test_csv_invalid_is_unknown(data):
    result = state_summary(io.BytesIO(data))
    assert result["status"] == "invalid"
    assert result["row_count"] is None


def test_csv_limits_and_empty_data():
    result = state_summary(io.BytesIO(b"x" * (SUMMARY_BYTES + 1)))
    assert result["status"] == "too_large"
    assert result["row_count"] is None
    result = state_summary(io.BytesIO(b"a\n" + b"1\n" * (SUMMARY_ROWS + 1)))
    assert result["row_count"] == SUMMARY_ROWS
    assert result["truncated"] is True
    assert len(result["preview"]) == 5
    assert state_summary(io.BytesIO(b"a,b\n"))["row_count"] == 0
