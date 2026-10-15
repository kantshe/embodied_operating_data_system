from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3

from fastapi.testclient import TestClient
import pytest

from app.database import connect, initialize_database
from app.main import create_app
from app import quality_checks
from test_episode_detail import record


def change(database, sql, values=()):
    with closing(connect(database)) as connection:
        with connection:
            connection.execute(sql, values)


def codes(report):
    return {item["code"] for item in report["issues"]}


@pytest.mark.parametrize("start,end,expected", [
    ("2026-10-15T08:00:00+08:00", "2026-10-15T00:00:01Z", None),
    ("2026-10-15T00:00:00Z", "2026-10-15T00:00:00Z", "invalid_time_range"),
    ("2026-10-15T00:00:01Z", "2026-10-15T00:00:00Z", "invalid_time_range"),
    ("2026-10-15T00:00:00", "2026-10-15T00:00:01Z", "invalid_datetime"),
    ("not-a-date", "2026-10-15T00:00:01Z", "invalid_datetime"),
    ("2026-10-15T00:00:00Z", "2026-02-30T00:00:01Z", "invalid_datetime"),
    (None, "2026-10-15T00:00:01Z", "required_field"),
])
def test_record_times(record, start, end, expected):
    client, url, database, *_ = record
    change(database, "UPDATE episodes SET started_at=?,ended_at=?", (start, end))
    report = client.post(url + "/quality-check").json()
    assert report["passed"] is (expected is None)
    if expected:
        assert expected in codes(report)


@pytest.mark.parametrize("content,expected,row", [
    (b"\xef\xbb\xbftimestamp_s,x\n0,1\n1e0,2\n", None, None),
    (b"timestamp_s\n0\n0\n", "timestamp_not_increasing", 3),
    (b"timestamp_s\n1\n0\n", "timestamp_not_increasing", 3),
    (b"timestamp_s\n-0.1\n", "negative_timestamp", 2),
    (b"timestamp_s\n1.000001\n", "timestamp_out_of_range", 2),
    (b"timestamp_s\n1e999999\n", "timestamp_out_of_range", 2),
    (b"timestamp_s\n1e999999999999999999999999\n", "invalid_timestamp", 2),
    (b"timestamp_s\nNaN\n", "invalid_timestamp", 2),
    (b"timestamp_s\ninf\n", "invalid_timestamp", 2),
    (b"timestamp_s\n1_0\n", "invalid_timestamp", 2),
    (b'timestamp_s\n""\n', "invalid_timestamp", 2),
    (b"timestamp_s,x\n,2\n", "invalid_timestamp", 2),
    (b"timestamp_s,x\n0\n", "invalid_csv", 2),
    (b'timestamp_s,x\n"0,2\n', "invalid_csv", None),
    (b"timestamp_s,x\n0,\xff\n", "invalid_csv", None),
    (b"timestamp_s,timestamp_s\n0,1\n", "invalid_csv", None),
    (b"time,x\n0,1\n", "missing_timestamp", None),
    (b"timestamp_s\n", "empty_sequence", None),
])
def test_sequence_checks(record, content, expected, row):
    client, url, _, storage, _ = record
    (storage / "batch/state.csv").write_bytes(content)
    report = client.post(url + "/quality-check").json()
    assert report["passed"] is (expected is None)
    if expected:
        issue = next(item for item in report["issues"] if item["code"] == expected)
        assert issue["asset_id"] == "state"
        if row is not None:
            assert issue["row"] == row


def test_exact_fractional_boundary_and_beyond_preview(record):
    client, url, database, storage, _ = record
    change(database, "UPDATE episodes SET ended_at='2026-10-14T01:00:00.123456Z'")
    path = storage / "batch/state.csv"
    path.write_text("timestamp_s\n0\n0.123456\n")
    assert client.post(url + "/quality-check").json()["passed"]
    path.write_text("timestamp_s\n" + "".join(f"{i / 1000000:.6f}\n" for i in range(10001)) + "0.2\n")
    assert client.get(url).json()["assets"][0]["state_summary"]["truncated"]
    report = client.post(url + "/quality-check").json()
    assert report["issues"] == [{"code": "timestamp_out_of_range", "field": "timestamp_s", "asset_id": "state", "row": 10003}]


@pytest.mark.parametrize("limit,value,content,code", [
    ("MAX_SEQUENCE_BYTES", 10, b"timestamp_s\n0\n", "sequence_limit"),
    ("MAX_SEQUENCE_ROWS", 1, b"timestamp_s\n0\n1\n", "sequence_limit"),
    ("MAX_ISSUES", 2, b"timestamp_s\n0\n0\n0\n0\n", "issue_limit"),
])
def test_limits_never_pass(record, monkeypatch, limit, value, content, code):
    client, url, _, storage, _ = record
    monkeypatch.setattr(quality_checks, limit, value)
    (storage / "batch/state.csv").write_bytes(content)
    report = client.post(url + "/quality-check").json()
    assert not report["complete"] and not report["passed"]
    assert code in codes(report)
    assert client.get(url + "/quality-reports/" + report["id"]).json() == report


def test_report_history_persists_and_is_scoped(record):
    client, url, database, storage, other = record
    first = client.post(url + "/quality-check").json()
    (storage / "batch/state.csv").write_text("timestamp_s\n-1\n")
    second = client.post(url + "/quality-check").json()
    assert first["passed"] and not second["passed"]
    assert client.get(url + "/quality-reports?limit=1").json() == [second]
    assert client.get(url + "/quality-reports?limit=1&offset=1").json() == [first]
    with TestClient(create_app(database)) as restarted:
        assert restarted.get(url + "/quality-reports/" + first["id"]).json() == first
    foreign = f"/api/projects/{other['id']}/episodes/episode"
    assert client.get(foreign + "/quality-reports").status_code == 404
    assert client.get(foreign + "/quality-reports/" + first["id"]).status_code == 404
    change(database, "INSERT INTO episodes (id,task_id,created_at) SELECT 'second',task_id,created_at FROM episodes")
    assert client.get(url.rsplit("/", 1)[0] + "/second/quality-reports/" + first["id"]).status_code == 404
    assert client.get(url + "/quality-reports/absent").status_code == 404


def test_annotation_changes_noop_conflict_and_restart(record):
    client, url, database, *_ = record
    payload = {"outcome": "failure", "failure_reason": "  missed object  ", "expected_version": 0}
    response = client.patch(url + "/annotation", json=payload)
    assert response.status_code == 200
    assert response.json() == {"id": "episode", "outcome": "failure", "failure_reason": "missed object", "annotation_version": 1, "changed": True}
    history = client.get(url + "/annotations").json()
    assert len(history) == 2
    assert {item["revision"] for item in history} == {1}
    assert {item["changed_at"] for item in history} == {history[0]["changed_at"]}
    assert {(item["field_name"], item["previous_value"], item["current_value"]) for item in history} == {
        ("outcome", "success", "failure"), ("failure_reason", None, "missed object"),
    }
    assert client.patch(url + "/annotation", json=payload).status_code == 409
    payload["expected_version"] = 1
    assert not client.patch(url + "/annotation", json=payload).json()["changed"]
    assert client.get(url + "/annotations").json() == history
    assert client.patch(url + "/annotation", json={"outcome": "success", "expected_version": 1}).status_code == 200
    detail = client.get(url).json()
    assert detail["failure_reason"] is None and detail["annotation_version"] == 2
    latest = client.get(url + "/annotations?limit=2").json()
    assert {item["revision"] for item in latest} == {2}
    assert client.get(url + "/annotations?limit=2&offset=2").json() == history
    with TestClient(create_app(database)) as restarted:
        assert restarted.get(url).json()["annotation_version"] == 2
        assert len(restarted.get(url + "/annotations").json()) == 4


@pytest.mark.parametrize("payload", [
    {"outcome": "unknown", "expected_version": 0},
    {"outcome": "failure", "expected_version": 0},
    {"outcome": "failure", "failure_reason": "  ", "expected_version": 0},
    {"outcome": "failure", "failure_reason": "x" * 2001, "expected_version": 0},
    {"outcome": "success", "failure_reason": "bad", "expected_version": 0},
    {"outcome": "success"},
    {"outcome": "success", "expected_version": -1},
    {"outcome": "success", "expected_version": True},
    {"outcome": "success", "expected_version": "0"},
    {"outcome": "success", "expected_version": 0, "device_id": "changed"},
])
def test_invalid_annotation_does_not_write(record, payload):
    client, url, *_ = record
    assert client.patch(url + "/annotation", json=payload).status_code == 422
    assert client.get(url + "/annotations").json() == []
    assert client.get(url).json()["annotation_version"] == 0


def test_annotation_ownership_and_concurrent_writers(record):
    client, url, _, _, other = record
    foreign = f"/api/projects/{other['id']}/episodes/episode"
    assert client.get(foreign + "/annotations").status_code == 404
    assert client.patch(foreign + "/annotation", json={"outcome": "pending", "expected_version": 0}).status_code == 404
    def save(outcome):
        return client.patch(url + "/annotation", json={"outcome": outcome, "failure_reason": "reason" if outcome == "failure" else None, "expected_version": 0}).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(save, ("pending", "failure"))) == [200, 409]
    assert client.get(url).json()["annotation_version"] == 1


@pytest.mark.parametrize("route", ["annotations", "quality-reports"])
@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "offset=9223372036854775808"])
def test_history_query_bounds(record, route, query):
    client, url, *_ = record
    assert client.get(url + f"/{route}?{query}").status_code == 422


@pytest.mark.parametrize("table,route", [("annotation_revisions", "annotation"), ("quality_reports", "quality-check")])
def test_failed_history_write_rolls_back(record, table, route):
    _, url, database, *_ = record
    change(database, f"CREATE TRIGGER reject_write BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with TestClient(create_app(database), raise_server_exceptions=False) as client:
        response = client.patch(url + "/annotation", json={"outcome": "pending", "expected_version": 0}) if route == "annotation" else client.post(url + "/quality-check")
        assert response.status_code == 500
        assert client.get(url).json()["outcome"] == "success"
        assert client.get(url).json()["annotation_version"] == 0
        assert client.get(url + "/annotations").json() == []
        assert client.get(url + "/quality-reports").json() == []


def test_old_database_migration_preserves_records(tmp_path):
    database = tmp_path / "old.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript("""
            CREATE TABLE episodes (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, import_batch_id TEXT,
                device_id TEXT, started_at TEXT, ended_at TEXT, outcome TEXT NOT NULL DEFAULT 'pending',
                source_type TEXT NOT NULL DEFAULT 'synthetic', created_at TEXT NOT NULL);
            CREATE TABLE annotation_revisions (id TEXT PRIMARY KEY, episode_id TEXT NOT NULL,
                field_name TEXT NOT NULL, previous_value TEXT, current_value TEXT, changed_at TEXT NOT NULL);
            INSERT INTO episodes (id, task_id, outcome, created_at) VALUES ('old','task','failure','2026-10-14');
            INSERT INTO annotation_revisions VALUES ('revision','old','outcome','pending','failure','2026-10-14');
        """)
    initialize_database(database)
    initialize_database(database)
    with closing(connect(database)) as connection:
        assert connection.execute("SELECT id,outcome,failure_reason,annotation_version FROM episodes").fetchall() == [("old", "failure", None, 0)]
        assert connection.execute("SELECT id,current_value,revision FROM annotation_revisions").fetchall() == [("revision", "failure", 0)]
