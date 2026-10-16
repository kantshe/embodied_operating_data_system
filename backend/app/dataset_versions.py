from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.database import connect
from app.episode_detail import open_asset


class VersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    episode_ids: Annotated[list[Annotated[str, StringConstraints(min_length=1, max_length=200)]], Field(min_length=1, max_length=500)]

    @field_validator("episode_ids")
    @classmethod
    def unique_ids(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("Duplicate episode IDs")
        return values


def version_router(database_path: Path, storage_root: Path):
    router = APIRouter(prefix="/api/projects/{project_id}/versions")

    def project(connection, project_id):
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Project not found")
        return dict(row)

    def result(row, detail=False):
        data = dict(row)
        snapshot = json.loads(data.pop("snapshot"))
        data["episode_count"] = len(snapshot["episodes"])
        if detail:
            data["snapshot"] = snapshot
        return data

    @router.post("", status_code=201)
    def create(project_id: str, payload: VersionCreate):
        with closing(connect(database_path)) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                project_data = project(connection, project_id)
                episodes = []
                # Validate the entire selection before reading files or creating a version.
                for episode_id in payload.episode_ids:
                    row = connection.execute(
                        "SELECT e.* FROM episodes e JOIN tasks t ON t.id=e.task_id "
                        "WHERE e.id=? AND t.project_id=?", (episode_id, project_id),
                    ).fetchone()
                    if row is None:
                        raise HTTPException(404, "Selected episode not found in project")
                    episodes.append(dict(row))
                for episode in episodes:
                    episode["task"] = dict(connection.execute("SELECT * FROM tasks WHERE id=?", (episode["task_id"],)).fetchone())
                    assets = [dict(row) for row in connection.execute("SELECT * FROM assets WHERE episode_id=? ORDER BY id", (episode["id"],))]
                    if not assets:
                        raise HTTPException(409, "Selected episode has no assets")
                    for asset in assets:
                        try:
                            with open_asset(storage_root, asset["path"]) as stream:
                                before = os.fstat(stream.fileno())
                                digest = hashlib.sha256()
                                size = 0
                                while chunk := stream.read(1024 * 1024):
                                    digest.update(chunk)
                                    size += len(chunk)
                                after = os.fstat(stream.fileno())
                                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or size != after.st_size:
                                    raise HTTPException(409, "Asset changed during snapshot")
                                checksum = digest.hexdigest()
                                if (asset["checksum"] is not None and asset["checksum"] != checksum) or (asset["size_bytes"] is not None and asset["size_bytes"] != size):
                                    raise HTTPException(409, "Asset differs from imported metadata")
                                asset.update(size_bytes=size, checksum=checksum)
                        except (OSError, ValueError) as error:
                            raise HTTPException(409, "Asset missing or unreadable") from error
                    episode["assets"] = assets
                    report = connection.execute("SELECT * FROM quality_reports WHERE episode_id=? ORDER BY checked_at DESC,rowid DESC LIMIT 1", (episode["id"],)).fetchone()
                    episode["quality_report"] = dict(report) if report else None
                    if report:
                        episode["quality_report"]["issues"] = json.loads(report["issues"])
                snapshot = {"schema_version": 1, "project": project_data, "episodes": episodes}
                data = {"id": str(uuid4()), "project_id": project_id, "name": payload.name,
                        "snapshot": json.dumps(snapshot, ensure_ascii=False), "created_at": datetime.now(timezone.utc).isoformat()}
                connection.execute("INSERT INTO dataset_versions (id,project_id,name,snapshot,created_at) VALUES (:id,:project_id,:name,:snapshot,:created_at)", data)
        return result(data, True)

    @router.get("")
    def versions(project_id: str, limit: Annotated[int, Query(ge=1, le=100)] = 20,
                 offset: Annotated[int, Query(ge=0, le=9223372036854775807)] = 0):
        with closing(connect(database_path)) as connection:
            project(connection, project_id)
            return [result(row) for row in connection.execute(
                "SELECT * FROM dataset_versions WHERE project_id=? ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?",
                (project_id, limit, offset),
            )]

    @router.get("/{version_id}")
    def detail(project_id: str, version_id: str):
        with closing(connect(database_path)) as connection:
            project(connection, project_id)
            row = connection.execute("SELECT * FROM dataset_versions WHERE id=? AND project_id=?", (version_id, project_id)).fetchone()
        if row is None:
            raise HTTPException(404, "Version not found")
        return result(row, True)

    return router
