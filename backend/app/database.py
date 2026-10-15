import sqlite3
from contextlib import closing
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(database_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(database_path: str | Path) -> None:
    with closing(connect(database_path)) as connection:
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        # Existing checkouts predate annotation fields; add them without rewriting rows.
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            for table, additions in {
                "episodes": {"failure_reason": "TEXT", "annotation_version": "INTEGER NOT NULL DEFAULT 0"},
                "annotation_revisions": {"revision": "INTEGER NOT NULL DEFAULT 0"},
            }.items():
                columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                for name, definition in additions.items():
                    if name not in columns:
                        connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
