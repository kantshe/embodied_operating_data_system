from datetime import timezone
from pathlib import PurePosixPath
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints,
    field_validator, model_validator,
)

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssetReference(ManifestModel):
    path: NonEmptyText
    asset_type: Literal["video", "state", "metadata"]

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        parts = value.split("/")
        if (
            PurePosixPath(value).is_absolute()
            or any(part in ("", ".", "..") for part in parts)
            or "\\" in value or ":" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("Use a relative POSIX path without traversal")
        return value

    @model_validator(mode="after")
    def validate_extension(self):
        suffix = PurePosixPath(self.path).suffix.lower()
        expected = {"video": ".mp4", "state": ".csv", "metadata": ".json"}
        if suffix != expected[self.asset_type]:
            raise ValueError("File extension does not match asset_type")
        return self


class EpisodeEntry(ManifestModel):
    id: UUID
    task_id: UUID
    device_id: NonEmptyText
    started_at: AwareDatetime
    ended_at: AwareDatetime
    outcome: Literal["pending", "success", "failure"] = "pending"
    source_type: Literal["synthetic", "collected"]
    assets: list[AssetReference] = Field(min_length=1)

    @field_validator("started_at", "ended_at", mode="before")
    @classmethod
    def require_timestamp_text(cls, value):
        if not isinstance(value, str):
            raise ValueError("Use an ISO 8601 timestamp string with timezone")
        return value

    @model_validator(mode="after")
    def validate_episode(self):
        if self.ended_at <= self.started_at:
            raise ValueError("ended_at must be later than started_at")
        paths = [asset.path for asset in self.assets]
        if len(paths) != len(set(paths)):
            raise ValueError("Asset paths must be unique within an episode")
        self.started_at = self.started_at.astimezone(timezone.utc)
        self.ended_at = self.ended_at.astimezone(timezone.utc)
        return self


class ImportManifest(ManifestModel):
    schema_version: Literal["1.0"]
    source: NonEmptyText
    episodes: list[EpisodeEntry] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_unique_ids(self):
        ids = [episode.id for episode in self.episodes]
        if len(ids) != len(set(ids)):
            raise ValueError("Episode IDs must be unique within a manifest")
        return self


class ImportResult(BaseModel):
    id: str
    project_id: str
    source: str
    imported_at: AwareDatetime
    episode_ids: list[str]
    asset_count: int
