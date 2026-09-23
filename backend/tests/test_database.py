import sqlite3
from contextlib import closing

import pytest

from app.database import connect, initialize_database


def test_initialize_database_creates_domain_tables(tmp_path):
    database_path = tmp_path / "test.db"

    initialize_database(database_path)
    initialize_database(database_path)

    with closing(connect(database_path)) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {
        "projects",
        "tasks",
        "import_batches",
        "episodes",
        "assets",
        "quality_reports",
        "annotation_revisions",
        "dataset_versions",
    } <= tables


def test_connection_enforces_foreign_keys(tmp_path):
    database_path = tmp_path / "test.db"
    initialize_database(database_path)

    with closing(connect(database_path)) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(
                "INSERT INTO tasks (id, project_id, name, created_at) "
                "VALUES (?, ?, ?, ?)",
                ("task-1", "missing-project", "Pick", "2026-09-23T00:00:00Z"),
            )
