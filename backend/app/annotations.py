from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.database import connect


class AnnotationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["pending", "success", "failure"]
    failure_reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None
    expected_version: Annotated[int, Field(strict=True, ge=0)]

    @model_validator(mode="after")
    def validate_reason(self):
        self.failure_reason = self.failure_reason or None
        if self.outcome == "failure" and not self.failure_reason:
            raise ValueError("Failure reason is required")
        if self.outcome != "failure" and self.failure_reason:
            raise ValueError("Failure reason is only allowed for failure")
        return self


def annotation_router(database_path: Path):
    router = APIRouter(prefix="/api/projects/{project_id}/episodes/{episode_id}")

    def current(connection, project_id, episode_id):
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT e.id, e.outcome, e.failure_reason, e.annotation_version FROM episodes e "
            "JOIN tasks t ON e.task_id=t.id WHERE e.id=? AND t.project_id=?",
            (episode_id, project_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Episode not found")
        return dict(row)

    @router.patch("/annotation")
    def update(project_id: str, episode_id: str, payload: AnnotationUpdate):
        with closing(connect(database_path)) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                original = current(connection, project_id, episode_id)
                if original["annotation_version"] != payload.expected_version:
                    raise HTTPException(status_code=409, detail="Annotation changed; reload before saving")
                changes = [
                    (field, original[field], getattr(payload, field))
                    for field in ("outcome", "failure_reason")
                    if original[field] != getattr(payload, field)
                ]
                if not changes:
                    return {**original, "changed": False}
                version = original["annotation_version"] + 1
                changed_at = datetime.now(timezone.utc).isoformat()
                connection.execute(
                    "UPDATE episodes SET outcome=?, failure_reason=?, annotation_version=? WHERE id=?",
                    (payload.outcome, payload.failure_reason, version, episode_id),
                )
                connection.executemany(
                    "INSERT INTO annotation_revisions "
                    "(id,episode_id,field_name,previous_value,current_value,changed_at,revision) "
                    "VALUES (?,?,?,?,?,?,?)",
                    [(str(uuid4()), episode_id, field, old, new, changed_at, version)
                     for field, old, new in changes],
                )
                return {**current(connection, project_id, episode_id), "changed": True}

    @router.get("/annotations")
    def history(
        project_id: str, episode_id: str,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0, le=9223372036854775807)] = 0,
    ):
        with closing(connect(database_path)) as connection:
            current(connection, project_id, episode_id)
            return [dict(row) for row in connection.execute(
                "SELECT * FROM annotation_revisions WHERE episode_id=? "
                "ORDER BY revision DESC, changed_at DESC, rowid DESC LIMIT ? OFFSET ?",
                (episode_id, limit, offset),
            ).fetchall()]

    return router
