"""PostgreSQL adapter for FinGuard workspace persistence."""

from __future__ import annotations

import base64
import json
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

import psycopg
from psycopg.rows import dict_row

from finguard.workspace_store import WorkspaceStore, WorkspaceStoreError

PG_BIN = Path("D:/postgresql/18/bin")
BACKUP_MARKER = b"PGJSON1\n"
BACKUP_TABLES = (
    "organizations",
    "members",
    "audit_cases",
    "case_artifacts",
    "audit_logs",
    "auth_tokens",
    "browser_sessions",
    "rule_profiles",
    "ai_usage_events",
)


class _PgCursor:
    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor

    def fetchone(self) -> Any:
        return self.cursor.fetchone()

    def fetchall(self) -> list[Any]:
        return self.cursor.fetchall()


class _PgConnection:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def execute(self, sql: str, parameters: Any = ()) -> _PgCursor:
        translated = sql.replace("?", "%s")
        if "INSERT OR IGNORE INTO" in translated:
            translated = translated.replace("INSERT OR IGNORE INTO", "INSERT INTO")
            translated = translated.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        cursor = self.connection.cursor()
        cursor.execute(translated, parameters)
        return _PgCursor(cursor)

    def executescript(self, script: str) -> None:
        self.connection.execute(script)

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def close(self) -> None:
        self.connection.close()


class PostgresWorkspaceStore(WorkspaceStore):
    def __init__(self, database_url: str) -> None:
        self.database_url = str(database_url or "").strip()
        if not self.database_url:
            raise WorkspaceStoreError("FINGUARD_DATABASE_URL 未配置")
        self.initialize()
        self.bootstrap_local_workspace()

    @contextmanager
    def _connect(self) -> Iterator[_PgConnection]:
        try:
            connection = psycopg.connect(
                self.database_url,
                connect_timeout=10,
                row_factory=dict_row,
            )
        except Exception as exc:
            raise WorkspaceStoreError(f"无法连接 PostgreSQL：{type(exc).__name__}") from exc
        wrapper = _PgConnection(connection)
        try:
            yield wrapper
            wrapper.commit()
        except Exception:
            wrapper.rollback()
            raise
        finally:
            wrapper.close()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS organizations (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS members (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES organizations(id),
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    username TEXT,
                    password_hash TEXT,
                    password_salt TEXT,
                    email TEXT,
                    email_verified INTEGER NOT NULL DEFAULT 0,
                    email_verified_at TEXT,
                    password_changed_at TEXT,
                    failed_login_count INTEGER NOT NULL DEFAULT 0,
                    locked_until DOUBLE PRECISION,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_members_org_username
                    ON members(organization_id, username)
                    WHERE username IS NOT NULL;
                CREATE TABLE IF NOT EXISTS audit_cases (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES organizations(id),
                    title TEXT NOT NULL,
                    period TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'draft',
                    owner_id TEXT NOT NULL REFERENCES members(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_audit_cases_org_updated
                    ON audit_cases(organization_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS case_artifacts (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES audit_cases(id),
                    kind TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    content BYTEA NOT NULL,
                    created_by TEXT NOT NULL REFERENCES members(id),
                    created_at TEXT NOT NULL,
                    UNIQUE (case_id, kind, sha256)
                );
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id BIGSERIAL PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES organizations(id),
                    case_id TEXT REFERENCES audit_cases(id),
                    user_id TEXT NOT NULL REFERENCES members(id),
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    previous_hash TEXT,
                    entry_hash TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_tokens (
                    id TEXT PRIMARY KEY,
                    member_id TEXT NOT NULL REFERENCES members(id),
                    purpose TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    code_salt TEXT NOT NULL,
                    expires_at DOUBLE PRECISION NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 5,
                    used_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS browser_sessions (
                    id TEXT PRIMARY KEY,
                    member_id TEXT NOT NULL REFERENCES members(id),
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at DOUBLE PRECISION NOT NULL,
                    last_seen_at DOUBLE PRECISION NOT NULL,
                    expires_at DOUBLE PRECISION NOT NULL,
                    revoked_at DOUBLE PRECISION,
                    user_agent TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_browser_sessions_token
                    ON browser_sessions(token_hash);
                CREATE INDEX IF NOT EXISTS idx_browser_sessions_member
                    ON browser_sessions(member_id, expires_at DESC);
                CREATE TABLE IF NOT EXISTS rule_profiles (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES organizations(id),
                    name TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    config_json TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT NOT NULL REFERENCES members(id),
                    created_at TEXT NOT NULL,
                    UNIQUE (organization_id, version)
                );
                CREATE TABLE IF NOT EXISTS ai_usage_events (
                    id BIGSERIAL PRIMARY KEY,
                    organization_id TEXT NOT NULL REFERENCES organizations(id),
                    user_id TEXT NOT NULL REFERENCES members(id),
                    module TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_chars INTEGER NOT NULL,
                    redaction_count INTEGER NOT NULL DEFAULT 0,
                    estimated_tokens INTEGER NOT NULL,
                    estimated_cost DOUBLE PRECISION NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def database_integrity(self) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("SELECT 1").fetchone()
            counts = {}
            for table in (
                "organizations",
                "members",
                "audit_cases",
                "case_artifacts",
                "audit_logs",
                "browser_sessions",
                "rule_profiles",
                "ai_usage_events",
            ):
                row = connection.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()
                counts[table] = int(row["total"] if isinstance(row, dict) else row[0])
        return {"valid": True, "integrity": "ok", "counts": counts}

    def _connection_parts(self) -> tuple[str, dict[str, str]]:
        parsed = urlsplit(self.database_url)
        password = unquote(parsed.password or "")
        netloc = parsed.netloc
        if parsed.username:
            host = parsed.hostname or "localhost"
            port = f":{parsed.port}" if parsed.port else ""
            netloc = f"{parsed.username}@{host}{port}"
        safe_url = urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
        environment = dict(__import__("os").environ)
        if password:
            environment["PGPASSWORD"] = password
        return safe_url, environment

    def create_backup_bytes(self) -> bytes:
        if "pooler.supabase.com" in self.database_url:
            return self._create_logical_backup()
        pg_dump = PG_BIN / "pg_dump.exe"
        if not pg_dump.exists():
            raise WorkspaceStoreError("pg_dump.exe 未找到")
        safe_url, environment = self._connection_parts()
        result = subprocess.run(
            [
                str(pg_dump),
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                f"--dbname={safe_url}",
            ],
            env=environment,
            capture_output=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace")[:200]
            raise WorkspaceStoreError(f"PostgreSQL 备份失败：{detail}")
        return result.stdout

    def restore_backup_bytes(self, payload: bytes, *, user_id: str) -> Path:
        if bytes(payload).startswith(BACKUP_MARKER):
            return self._restore_logical_backup(payload)
        pg_restore = PG_BIN / "pg_restore.exe"
        if not pg_restore.exists():
            raise WorkspaceStoreError("pg_restore.exe 未找到")
        safe_url, environment = self._connection_parts()
        pre_restore = self.create_backup_bytes()
        backup_path = Path(f"postgres_pre_restore_{datetime.now():%Y%m%d_%H%M%S}.dump").resolve()
        backup_path.write_bytes(pre_restore)
        with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as handle:
            handle.write(bytes(payload))
            restore_path = Path(handle.name)
        try:
            result = subprocess.run(
                [
                    str(pg_restore),
                    "--clean",
                    "--if-exists",
                    "--no-owner",
                    "--no-privileges",
                    "--single-transaction",
                    f"--dbname={safe_url}",
                    str(restore_path),
                ],
                env=environment,
                capture_output=True,
                timeout=180,
                check=False,
            )
        finally:
            restore_path.unlink(missing_ok=True)
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace")[:200]
            raise WorkspaceStoreError(f"PostgreSQL 恢复失败：{detail}")
        self.initialize()
        return backup_path

    def _create_logical_backup(self) -> bytes:
        archive: dict[str, Any] = {
            "format": "finguard-postgres-json",
            "version": 1,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "tables": {},
        }
        with self._connect() as connection:
            for table in BACKUP_TABLES:
                rows = connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                serialized = []
                for row in rows:
                    item = dict(row)
                    if table == "case_artifacts" and item.get("content") is not None:
                        item["content"] = base64.b64encode(bytes(item["content"])).decode("ascii")
                    serialized.append(item)
                archive["tables"][table] = serialized
        return BACKUP_MARKER + json.dumps(
            archive,
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")

    def _restore_logical_backup(self, payload: bytes) -> Path:
        pre_restore_data = self._create_logical_backup()
        archive = json.loads(bytes(payload)[len(BACKUP_MARKER) :].decode("utf-8"))
        if archive.get("format") != "finguard-postgres-json":
            raise WorkspaceStoreError("不支持的逻辑备份格式")
        tables = archive.get("tables", {})
        self.initialize()
        with self._connect() as connection:
            connection.executescript(
                "TRUNCATE TABLE "
                + ", ".join(reversed(BACKUP_TABLES))
                + " RESTART IDENTITY CASCADE"
            )
            for table in BACKUP_TABLES:
                rows = list(tables.get(table, []))
                if not rows:
                    continue
                columns = list(rows[0].keys())
                for row in rows:
                    if table == "case_artifacts" and row.get("content"):
                        row["content"] = base64.b64decode(row["content"])
                placeholders = ", ".join(["%s"] * len(columns))
                values = [tuple(row.get(column) for column in columns) for row in rows]
                cursor = connection.connection.cursor()
                cursor.executemany(
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                    values,
                )
            connection.execute(
                """
                SELECT setval(
                    pg_get_serial_sequence('audit_logs', 'id'),
                    COALESCE((SELECT MAX(id) FROM audit_logs), 1),
                    EXISTS(SELECT 1 FROM audit_logs)
                )
                """
            )
            connection.execute(
                """
                SELECT setval(
                    pg_get_serial_sequence('ai_usage_events', 'id'),
                    COALESCE((SELECT MAX(id) FROM ai_usage_events), 1),
                    EXISTS(SELECT 1 FROM ai_usage_events)
                )
                """
            )
        backup_path = Path(
            f"postgres_pre_restore_{datetime.now():%Y%m%d_%H%M%S}.dump"
        ).resolve()
        backup_path.write_bytes(pre_restore_data)
        return backup_path
