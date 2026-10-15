from contextlib import closing
import csv
from datetime import datetime, timezone
import errno
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.database import connect
from app.quality_checks import (
    INCOMPLETE_CODES, RULE_VERSION, IssueLimit, Issues, check_sequence, record_duration,
)

SUMMARY_BYTES = 2 * 1024 * 1024
SUMMARY_ROWS = 10000


def open_asset(root: Path, relative: str):
    parts = relative.split("/")
    if (
        any(part in ("", ".", "..") for part in parts)
        or "\\" in relative or ":" in relative
        or any(ord(char) < 32 or ord(char) == 127 for char in relative)
    ):
        raise ValueError("Invalid stored path")
    directory = os.open(root.resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            next_directory = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            os.close(directory)
            directory = next_directory
        descriptor = os.open(
            parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory
        )
    finally:
        os.close(directory)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError("Not a regular file")
    return os.fdopen(descriptor, "rb")


def file_status(error: Exception) -> str:
    if isinstance(error, FileNotFoundError):
        return "missing"
    if isinstance(error, ValueError) or (
        isinstance(error, OSError) and error.errno in (errno.ELOOP, errno.ENOTDIR)
    ):
        return "invalid_path"
    return "unreadable"


def state_summary(stream) -> dict:
    data = stream.read(SUMMARY_BYTES + 1)
    if len(data) > SUMMARY_BYTES:
        return {"status": "too_large", "row_count": None, "columns": [], "preview": [], "truncated": True}
    try:
        reader = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
        columns = next(reader, [])
        if not columns or len(columns) > 64 or any(not value.strip() for value in columns):
            raise ValueError("Invalid CSV header")
        if len(set(columns)) != len(columns):
            raise ValueError("Duplicate CSV header")
        count = 0
        preview = []
        truncated = False
        for row in reader:
            if not row:
                continue
            if len(row) != len(columns):
                raise ValueError("Invalid CSV row")
            if count == SUMMARY_ROWS:
                truncated = True
                break
            count += 1
            if len(preview) < 5:
                preview.append([value[:256] for value in row])
        return {
            "status": "ready", "columns": columns, "row_count": count,
            "preview": preview, "truncated": truncated,
        }
    except (UnicodeError, csv.Error, ValueError):
        return {"status": "invalid", "row_count": None, "columns": [], "preview": [], "truncated": False}


def inspect_asset(storage_root: Path, asset: dict) -> dict:
    result = {**asset, "status": "available", "actual_size_bytes": None, "state_summary": None}
    try:
        with open_asset(storage_root, asset["path"]) as stream:
            result["actual_size_bytes"] = os.fstat(stream.fileno()).st_size
            if asset["asset_type"] == "state":
                result["state_summary"] = state_summary(stream)
    except (OSError, ValueError) as error:
        result["status"] = file_status(error)
    return result


def episode_router(database_path: Path, storage_root: Path) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}/episodes/{episode_id}")

    def load(project_id: str, episode_id: str, connection=None):
        if connection is None:
            with closing(connect(database_path)) as connection:
                return load(project_id, episode_id, connection)
        connection.row_factory = sqlite3.Row
        episode = connection.execute(
            "SELECT e.*, t.name AS task_name FROM episodes e "
            "JOIN tasks t ON e.task_id = t.id WHERE e.id = ? AND t.project_id = ?",
            (episode_id, project_id),
        ).fetchone()
        if episode is None:
            raise HTTPException(status_code=404, detail="Episode not found")
        assets = connection.execute(
            "SELECT * FROM assets WHERE episode_id = ? ORDER BY id", (episode_id,)
        ).fetchall()
        return dict(episode), [dict(asset) for asset in assets]

    @router.get("")
    def detail(project_id: str, episode_id: str):
        episode, assets = load(project_id, episode_id)
        return {**episode, "assets": [inspect_asset(storage_root, asset) for asset in assets]}

    @router.post("/quality-check")
    def check(project_id: str, episode_id: str):
        with closing(connect(database_path)) as connection:
            with connection:
                # Keep the inspected metadata stable until its report is committed.
                connection.execute("BEGIN IMMEDIATE")
                episode, assets = load(project_id, episode_id, connection)
                issues = Issues()
                try:
                    evaluate(episode, assets, issues)
                except IssueLimit:
                    pass
                report = {
                    "id": str(uuid4()), "episode_id": episode_id, "rule_version": RULE_VERSION,
                    "checked_at": datetime.now(timezone.utc).isoformat(), "issues": list(issues),
                }
                connection.execute(
                    "INSERT INTO quality_reports (id,episode_id,rule_version,issues,checked_at) "
                    "VALUES (?,?,?,?,?)",
                    (report["id"], episode_id, RULE_VERSION, json.dumps(issues), report["checked_at"]),
                )
        return report_result(report)

    def evaluate(episode, assets, issues):
        issue = issues.add
        for field in ("id", "task_id", "device_id", "started_at", "ended_at", "outcome", "source_type", "created_at"):
            value = episode[field]
            if value is None or not str(value).strip():
                issue("required_field", field)
        if episode["outcome"] not in ("pending", "success", "failure"):
            issue("invalid_value", "outcome")
        if episode["source_type"] not in ("synthetic", "collected"):
            issue("invalid_value", "source_type")
        if episode["outcome"] == "failure" and not (episode["failure_reason"] or "").strip():
            issue("required_field", "failure_reason")
        duration = record_duration(episode, issues)
        if not assets:
            issue("required_assets", "assets")
        for asset in assets:
            for field in ("path", "asset_type"):
                if not asset[field].strip():
                    issue("required_field", field, asset["id"])
            if asset["asset_type"] not in ("video", "state", "metadata"):
                issue("invalid_value", "asset_type", asset["id"])
            try:
                with open_asset(storage_root, asset["path"]) as stream:
                    before = os.fstat(stream.fileno())
                    if before.st_size == 0:
                        issue("empty_file", "path", asset["id"])
                    elif asset["asset_type"] == "state":
                        check_sequence(stream, duration, asset["id"], issues)
                    after = os.fstat(stream.fileno())
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                        after.st_size, after.st_mtime_ns, after.st_ctime_ns
                    ):
                        issue("file_changed", "path", asset["id"])
            except (OSError, ValueError) as error:
                issue("file_" + file_status(error), "path", asset["id"])

    def report_result(row):
        data = dict(row)
        if isinstance(data["issues"], str):
            data["issues"] = json.loads(data["issues"])
        return {**data, "passed": not data["issues"],
                "complete": not any(item["code"] in INCOMPLETE_CODES for item in data["issues"])}

    @router.get("/quality-reports")
    def reports(
        project_id: str, episode_id: str,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0, le=9223372036854775807)] = 0,
    ):
        with closing(connect(database_path)) as connection:
            load(project_id, episode_id, connection)
            rows = connection.execute(
                "SELECT * FROM quality_reports WHERE episode_id=? "
                "ORDER BY checked_at DESC, rowid DESC LIMIT ? OFFSET ?",
                (episode_id, limit, offset),
            ).fetchall()
        return [report_result(row) for row in rows]

    @router.get("/quality-reports/{report_id}")
    def report_detail(project_id: str, episode_id: str, report_id: str):
        with closing(connect(database_path)) as connection:
            load(project_id, episode_id, connection)
            row = connection.execute(
                "SELECT * FROM quality_reports WHERE id=? AND episode_id=?", (report_id, episode_id)
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Report not found")
        return report_result(row)

    @router.get("/assets/{asset_id}/content")
    def content(project_id: str, episode_id: str, asset_id: str,
                range_header: str | None = Header(default=None, alias="Range")):
        _, assets = load(project_id, episode_id)
        asset = next((item for item in assets if item["id"] == asset_id), None)
        if asset is None:
            raise HTTPException(status_code=404, detail="Asset not found")
        if asset["asset_type"] not in ("video", "state", "metadata"):
            raise HTTPException(status_code=415, detail="Unsupported asset type")
        try:
            stream = open_asset(storage_root, asset["path"])
            size = os.fstat(stream.fileno()).st_size
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=404, detail="Asset unavailable") from error
        start, end = 0, size - 1
        headers = {"Accept-Ranges": "bytes", "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
        if range_header:
            match = re.fullmatch(r"bytes=([0-9]{0,20})-([0-9]{0,20})", range_header)
            if match and any(match.groups()):
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                elif int(last) > 0:
                    start = max(0, size - int(last))
                else:
                    start = size
            else:
                start = size
            if start > end or start >= size:
                stream.close()
                raise HTTPException(status_code=416, detail="Invalid byte range",
                                    headers={"Content-Range": f"bytes */{size}"})
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(max(0, end - start + 1))
        stream.seek(start)

        def chunks():
            try:
                remaining = end - start + 1
                while remaining > 0:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
            finally:
                stream.close()

        return StreamingResponse(
            chunks(), status_code=206 if range_header else 200,
            media_type={"video": "video/mp4", "state": "text/csv", "metadata": "application/json"}[asset["asset_type"]],
            headers=headers, background=BackgroundTask(stream.close),
        )

    return router
