from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, StringConstraints

from app.database import connect, initialize_database
from app.import_manifest import ImportManifest, ImportResult

DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "system.db"


class ProjectCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    description: str | None = None


class Project(ProjectCreate):
    id: str
    created_at: datetime


class TaskCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    objective: str | None = None
    scene: str | None = None


class Task(TaskCreate):
    id: str
    project_id: str
    created_at: datetime


def create_app(database_path: Path = DEFAULT_DATABASE_PATH) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database_path.parent.mkdir(parents=True, exist_ok=True)
        initialize_database(database_path)
        yield

    app = FastAPI(
        title="Embodied Operating Data System",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/api/health")
    def health_check() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/projects", response_model=Project, status_code=201)
    def create_project(payload: ProjectCreate) -> Project:
        project = Project(
            **payload.model_dump(),
            id=str(uuid4()),
            created_at=datetime.now(timezone.utc),
        )
        with closing(connect(database_path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO projects (id, name, description, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        project.id,
                        project.name,
                        project.description,
                        project.created_at.isoformat(),
                    ),
                )
        return project

    @app.get("/api/projects", response_model=list[Project])
    def list_projects(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0, le=9223372036854775807)] = 0,
    ) -> list[Project]:
        with closing(connect(database_path)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT id, name, description, created_at FROM projects "
                "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [Project(**dict(row)) for row in rows]

    @app.get(
        "/api/projects/{project_id}",
        response_model=Project,
        responses={404: {"description": "Project not found"}},
    )
    def get_project(project_id: str) -> Project:
        with closing(connect(database_path)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT id, name, description, created_at FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return Project(**dict(row))

    @app.post(
        "/api/projects/{project_id}/tasks",
        response_model=Task,
        status_code=201,
        responses={404: {"description": "Project not found"}},
    )
    def create_task(project_id: str, payload: TaskCreate) -> Task:
        with closing(connect(database_path)) as connection:
            with connection:
                project = connection.execute(
                    "SELECT id FROM projects WHERE id = ?", (project_id,)
                ).fetchone()
                if project is None:
                    raise HTTPException(status_code=404, detail="Project not found")
                task = Task(
                    **payload.model_dump(),
                    id=str(uuid4()),
                    project_id=project_id,
                    created_at=datetime.now(timezone.utc),
                )
                connection.execute(
                    "INSERT INTO tasks (id, project_id, name, objective, scene, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        task.id,
                        task.project_id,
                        task.name,
                        task.objective,
                        task.scene,
                        task.created_at.isoformat(),
                    ),
                )
        return task

    @app.get(
        "/api/projects/{project_id}/tasks",
        response_model=list[Task],
        responses={404: {"description": "Project not found"}},
    )
    def list_tasks(
        project_id: str,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0, le=9223372036854775807)] = 0,
    ) -> list[Task]:
        with closing(connect(database_path)) as connection:
            connection.row_factory = sqlite3.Row
            project = connection.execute(
                "SELECT id FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if project is None:
                raise HTTPException(status_code=404, detail="Project not found")
            rows = connection.execute(
                "SELECT id, project_id, name, objective, scene, created_at "
                "FROM tasks WHERE project_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (project_id, limit, offset),
            ).fetchall()
        return [Task(**dict(row)) for row in rows]

    @app.post(
        "/api/projects/{project_id}/imports",
        response_model=ImportResult,
        status_code=201,
        responses={
            404: {"description": "Project not found"},
            409: {"description": "Episode ID already imported"},
            422: {"description": "Invalid manifest or task ownership"},
        },
    )
    def import_episodes(project_id: str, payload: ImportManifest) -> ImportResult:
        batch_id = str(uuid4())
        imported_at = datetime.now(timezone.utc)
        with closing(connect(database_path)) as connection:
            try:
                with connection:
                    # Hold one write transaction across ownership checks and inserts.
                    connection.execute("BEGIN IMMEDIATE")
                    project = connection.execute(
                        "SELECT id FROM projects WHERE id = ?", (project_id,)
                    ).fetchone()
                    if project is None:
                        raise HTTPException(status_code=404, detail="Project not found")
                    for task_id in {str(item.task_id) for item in payload.episodes}:
                        task = connection.execute(
                            "SELECT id FROM tasks WHERE id = ? AND project_id = ?",
                            (task_id, project_id),
                        ).fetchone()
                        if task is None:
                            raise HTTPException(
                                status_code=422,
                                detail=f"Task does not belong to project: {task_id}",
                            )
                    for episode in payload.episodes:
                        if connection.execute(
                            "SELECT id FROM episodes WHERE id = ?", (str(episode.id),)
                        ).fetchone():
                            raise HTTPException(
                                status_code=409,
                                detail=f"Episode ID already imported: {episode.id}",
                            )
                    connection.execute(
                        "INSERT INTO import_batches (id, project_id, source, imported_at) "
                        "VALUES (?, ?, ?, ?)",
                        (batch_id, project_id, payload.source, imported_at.isoformat()),
                    )
                    for episode in payload.episodes:
                        connection.execute(
                            "INSERT INTO episodes "
                            "(id, task_id, import_batch_id, device_id, started_at, ended_at, "
                            "outcome, source_type, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                str(episode.id), str(episode.task_id), batch_id,
                                episode.device_id, episode.started_at.isoformat(),
                                episode.ended_at.isoformat(), episode.outcome,
                                episode.source_type, imported_at.isoformat(),
                            ),
                        )
                        connection.executemany(
                            "INSERT INTO assets (id, episode_id, path, asset_type) "
                            "VALUES (?, ?, ?, ?)",
                            [
                                (str(uuid4()), str(episode.id), asset.path, asset.asset_type)
                                for asset in episode.assets
                            ],
                        )
            except sqlite3.IntegrityError as error:
                raise HTTPException(status_code=409, detail="Import conflict") from error
        return ImportResult(
            id=batch_id, project_id=project_id, source=payload.source,
            imported_at=imported_at,
            episode_ids=[str(episode.id) for episode in payload.episodes],
            asset_count=sum(len(episode.assets) for episode in payload.episodes),
        )

    return app


app = create_app()
