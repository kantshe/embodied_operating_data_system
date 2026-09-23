CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    objective TEXT,
    scene TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_batches (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    source TEXT NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    import_batch_id TEXT REFERENCES import_batches(id),
    device_id TEXT,
    started_at TEXT,
    ended_at TEXT,
    outcome TEXT NOT NULL DEFAULT 'pending',
    source_type TEXT NOT NULL DEFAULT 'synthetic',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES episodes(id),
    path TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    size_bytes INTEGER,
    checksum TEXT
);

CREATE TABLE IF NOT EXISTS quality_reports (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES episodes(id),
    rule_version TEXT NOT NULL,
    issues TEXT NOT NULL DEFAULT '[]',
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS annotation_revisions (
    id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL REFERENCES episodes(id),
    field_name TEXT NOT NULL,
    previous_value TEXT,
    current_value TEXT,
    changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_versions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    name TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_project_id ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_episodes_task_id ON episodes(task_id);
CREATE INDEX IF NOT EXISTS idx_assets_episode_id ON assets(episode_id);
