from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
from uuid import uuid4

from fastapi import HTTPException

from app.database import connect
from app.import_manifest import EntryResult, ImportManifest, ImportResult


def copy_asset(root: Path, relative: str, destination: Path) -> tuple[int, str]:
    # Open each component relative to its directory descriptor to prevent
    # symlink replacement from redirecting a file read outside the source root.
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = relative.split("/")
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
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Asset is not a regular file")
        destination.parent.mkdir(parents=True, exist_ok=True)
        checksum = hashlib.sha256()
        size = 0
        with destination.open("xb") as output:
            while chunk := stream.read(1024 * 1024):
                output.write(chunk)
                checksum.update(chunk)
                size += len(chunk)
        return size, checksum.hexdigest()


def import_batch(database_path: Path, source_root: Path, storage_root: Path,
                 project_id: str, payload: ImportManifest) -> ImportResult:
    batch_id = str(uuid4())
    imported_at = datetime.now(timezone.utc)
    batch_directory = storage_root / batch_id
    results = []
    episode_ids = []
    asset_count = 0
    try:
        with closing(connect(database_path)) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                if not connection.execute(
                    "SELECT id FROM projects WHERE id = ?", (project_id,)
                ).fetchone():
                    raise HTTPException(status_code=404, detail="Project not found")
                connection.execute(
                    "INSERT INTO import_batches (id, project_id, source, imported_at) "
                    "VALUES (?, ?, ?, ?)",
                    (batch_id, project_id, payload.source, imported_at.isoformat()),
                )
                for index, episode in enumerate(payload.episodes):
                    episode_id = str(episode.id)
                    directory = batch_directory / str(index)
                    task = connection.execute(
                        "SELECT id FROM tasks WHERE id = ? AND project_id = ?",
                        (str(episode.task_id), project_id),
                    ).fetchone()
                    existing = connection.execute(
                        "SELECT t.project_id FROM episodes e JOIN tasks t ON t.id = e.task_id "
                        "WHERE e.id = ?", (episode_id,),
                    ).fetchone()
                    if not task:
                        result = EntryResult(index=index, episode_id=episode_id,
                                             status="failed", detail="Task does not belong to project")
                    elif existing:
                        result = EntryResult(
                            index=index, episode_id=episode_id,
                            status="skipped" if existing[0] == project_id else "failed",
                            detail="Episode ID already exists",
                        )
                    else:
                        try:
                            saved_assets = []
                            for asset in episode.assets:
                                asset_id = str(uuid4())
                                stored_path = Path(batch_id) / str(index) / (
                                    asset_id + Path(asset.path).suffix.lower()
                                )
                                size, checksum = copy_asset(
                                    source_root.resolve(), asset.path, storage_root / stored_path
                                )
                                saved_assets.append((
                                    asset_id, episode_id, stored_path.as_posix(),
                                    asset.asset_type, size, checksum,
                                ))
                        except (OSError, ValueError):
                            if directory.exists():
                                shutil.rmtree(directory)
                            result = EntryResult(
                                index=index, episode_id=episode_id, status="failed",
                                detail="Asset missing, unreadable or outside import directory",
                            )
                        else:
                            connection.execute(
                                "INSERT INTO episodes "
                                "(id, task_id, import_batch_id, device_id, started_at, ended_at, "
                                "outcome, source_type, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (episode_id, str(episode.task_id), batch_id, episode.device_id,
                                 episode.started_at.isoformat(), episode.ended_at.isoformat(),
                                 episode.outcome, episode.source_type, imported_at.isoformat()),
                            )
                            connection.executemany(
                                "INSERT INTO assets "
                                "(id, episode_id, path, asset_type, size_bytes, checksum) "
                                "VALUES (?, ?, ?, ?, ?, ?)", saved_assets,
                            )
                            episode_ids.append(episode_id)
                            asset_count += len(saved_assets)
                            result = EntryResult(index=index, episode_id=episode_id, status="imported")
                    results.append(result)
                connection.execute(
                    "INSERT INTO import_results (import_batch_id, results) VALUES (?, ?)",
                    (batch_id, json.dumps([result.model_dump() for result in results])),
                )
    except Exception as error:
        if batch_directory.exists():
            shutil.rmtree(batch_directory)
        if isinstance(error, sqlite3.IntegrityError):
            raise HTTPException(status_code=409, detail="Import conflict") from error
        raise
    return ImportResult(
        id=batch_id, project_id=project_id, source=payload.source,
        imported_at=imported_at, episode_ids=episode_ids,
        asset_count=asset_count, results=results,
    )
