from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel, StringConstraints

from app.database import connect, initialize_database

DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "system.db"


class ProjectCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    description: str | None = None


class Project(ProjectCreate):
    id: str
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

    return app


app = create_app()
