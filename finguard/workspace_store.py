"""SQLite persistence for audit projects, evidence artifacts, and operation logs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_ORGANIZATION_ID = "local-organization"
DEFAULT_USER_ID = "local-admin"
DEFAULT_USER_NAME = "本地管理员"
MAX_ARTIFACT_BYTES = 25 * 1024 * 1024
CASE_STATUS_ORDER = ("draft", "active", "review", "closed", "archived")
VALID_CASE_STATUSES = set(CASE_STATUS_ORDER)
CASE_STATUS_LABELS = {
    "draft": "草稿",
    "active": "进行中",
    "review": "待复核",
    "closed": "已结项",
    "archived": "已归档",
}


class WorkspaceStoreError(RuntimeError):
    """Raised when workspace persistence cannot safely complete an operation."""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _first_value(row: Any) -> Any:
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


class WorkspaceStore:
    """A small, migration-friendly SQLite repository for local audit work."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()
        self.bootstrap_local_workspace()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(3):
            try:
                connection = sqlite3.connect(str(self.database_path), timeout=20)
                break
            except sqlite3.OperationalError as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.2 * (attempt + 1))
        if connection is None:
            raise WorkspaceStoreError(f"无法打开工作区数据库：{last_error}") from last_error
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout = 20000")
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connect() as connection:
            try:
                connection.execute("PRAGMA journal_mode = WAL")
            except sqlite3.OperationalError:
                connection.execute("PRAGMA journal_mode = DELETE")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS organizations (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS members (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
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
                    locked_until REAL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (organization_id) REFERENCES organizations(id)
                );

                CREATE TABLE IF NOT EXISTS audit_cases (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    period TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'draft',
                    owner_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (organization_id) REFERENCES organizations(id),
                    FOREIGN KEY (owner_id) REFERENCES members(id)
                );

                CREATE INDEX IF NOT EXISTS idx_audit_cases_org_updated
                    ON audit_cases(organization_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS case_artifacts (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    content BLOB NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (case_id) REFERENCES audit_cases(id),
                    FOREIGN KEY (created_by) REFERENCES members(id),
                    UNIQUE (case_id, kind, sha256)
                );

                CREATE INDEX IF NOT EXISTS idx_case_artifacts_case_created
                    ON case_artifacts(case_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    organization_id TEXT NOT NULL,
                    case_id TEXT,
                    user_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    previous_hash TEXT,
                    entry_hash TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (organization_id) REFERENCES organizations(id),
                    FOREIGN KEY (case_id) REFERENCES audit_cases(id),
                    FOREIGN KEY (user_id) REFERENCES members(id)
                );

                CREATE INDEX IF NOT EXISTS idx_audit_logs_case_created
                    ON audit_logs(case_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS auth_tokens (
                    id TEXT PRIMARY KEY,
                    member_id TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    code_hash TEXT NOT NULL,
                    code_salt TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 5,
                    used_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (member_id) REFERENCES members(id)
                );

                CREATE INDEX IF NOT EXISTS idx_auth_tokens_member_created
                    ON auth_tokens(member_id, purpose, created_at DESC);

                CREATE TABLE IF NOT EXISTS rule_profiles (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    config_json TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (organization_id) REFERENCES organizations(id),
                    FOREIGN KEY (created_by) REFERENCES members(id),
                    UNIQUE (organization_id, version)
                );

                CREATE INDEX IF NOT EXISTS idx_rule_profiles_org_version
                    ON rule_profiles(organization_id, version DESC);

                CREATE TABLE IF NOT EXISTS ai_usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    organization_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    module TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_chars INTEGER NOT NULL,
                    redaction_count INTEGER NOT NULL DEFAULT 0,
                    estimated_tokens INTEGER NOT NULL,
                    estimated_cost REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (organization_id) REFERENCES organizations(id),
                    FOREIGN KEY (user_id) REFERENCES members(id)
                );

                CREATE INDEX IF NOT EXISTS idx_ai_usage_org_created
                    ON ai_usage_events(organization_id, created_at DESC);
                """
            )
            self._ensure_column(connection, "organizations", "slug", "TEXT")
            self._ensure_column(connection, "members", "username", "TEXT")
            self._ensure_column(connection, "members", "password_hash", "TEXT")
            self._ensure_column(connection, "members", "password_salt", "TEXT")
            self._ensure_column(connection, "members", "email", "TEXT")
            self._ensure_column(
                connection,
                "members",
                "email_verified",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(connection, "members", "email_verified_at", "TEXT")
            self._ensure_column(connection, "members", "password_changed_at", "TEXT")
            self._ensure_column(
                connection,
                "members",
                "failed_login_count",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(connection, "members", "locked_until", "REAL")
            self._ensure_column(connection, "audit_logs", "previous_hash", "TEXT")
            self._ensure_column(connection, "audit_logs", "entry_hash", "TEXT")
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_organizations_slug
                ON organizations(slug)
                WHERE slug IS NOT NULL
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_members_org_username
                ON members(organization_id, username)
                WHERE username IS NOT NULL
                """
            )

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def bootstrap_local_workspace(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO organizations (id, name, slug, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (DEFAULT_ORGANIZATION_ID, "本地企业空间", "local", _now()),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO members
                    (id, organization_id, display_name, role, active, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (
                    DEFAULT_USER_ID,
                    DEFAULT_ORGANIZATION_ID,
                    DEFAULT_USER_NAME,
                    "local_admin",
                    _now(),
                ),
            )

    def create_organization(self, *, name: str, slug: str) -> dict[str, Any]:
        clean_name = str(name or "").strip()
        clean_slug = str(slug or "").strip().casefold()
        if not clean_name:
            raise ValueError("企业名称不能为空")
        if not clean_slug or any(character.isspace() for character in clean_slug):
            raise ValueError("企业标识必须是非空且不含空格的字符串")
        organization_id = f"ORG-{uuid.uuid4().hex[:16].upper()}"
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO organizations (id, name, slug, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (organization_id, clean_name, clean_slug, _now()),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("企业标识已存在") from exc
        return self.get_organization(organization_id) or {}

    def get_organization(self, organization_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM organizations WHERE id = ?",
                (str(organization_id),),
            ).fetchone()
        return _row_dict(row)

    def list_login_organizations(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT o.*
                FROM organizations o
                JOIN members m ON m.organization_id = o.id
                WHERE m.active = 1
                  AND m.username IS NOT NULL
                  AND m.password_hash IS NOT NULL
                  AND m.password_salt IS NOT NULL
                ORDER BY o.name
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def create_member(
        self,
        *,
        organization_id: str,
        username: str,
        display_name: str,
        role: str,
        password_hash: str,
        password_salt: str,
    ) -> dict[str, Any]:
        if self.get_organization(organization_id) is None:
            raise ValueError("企业空间不存在")
        clean_username = str(username or "").strip().casefold()
        clean_display_name = str(display_name or "").strip()
        if not clean_username or any(character.isspace() for character in clean_username):
            raise ValueError("用户名不能为空且不能包含空格")
        if not clean_display_name:
            raise ValueError("成员姓名不能为空")
        member_id = f"USR-{uuid.uuid4().hex[:16].upper()}"
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO members
                        (id, organization_id, display_name, role, username,
                         password_hash, password_salt, active, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        member_id,
                        str(organization_id),
                        clean_display_name,
                        str(role),
                        clean_username,
                        str(password_hash),
                        str(password_salt),
                        _now(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("当前企业已存在同名用户") from exc
        return self.get_member(member_id) or {}

    def get_member(self, member_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, organization_id, display_name, role, username,
                       email, email_verified, email_verified_at,
                       password_changed_at, failed_login_count, locked_until,
                       active, created_at
                FROM members
                WHERE id = ?
                """,
                (str(member_id),),
            ).fetchone()
        return _row_dict(row)

    def get_member_credentials(
        self,
        *,
        organization_id: str,
        username: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM members
                WHERE organization_id = ?
                  AND username = ?
                  AND active = 1
                """,
                (str(organization_id), str(username or "").strip().casefold()),
            ).fetchone()
        return _row_dict(row)

    def list_members(self, organization_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, organization_id, display_name, role, username,
                       email, email_verified, email_verified_at,
                       password_changed_at, failed_login_count, locked_until,
                       active, created_at
                FROM members
                WHERE organization_id = ?
                ORDER BY active DESC, created_at
                """,
                (str(organization_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_member(
        self,
        member_id: str,
        *,
        role: str,
        active: bool,
        organization_id: str,
    ) -> dict[str, Any]:
        member = self.get_member(member_id)
        if member is None or member["organization_id"] != organization_id:
            raise WorkspaceStoreError("成员不存在")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE members
                SET role = ?, active = ?
                WHERE id = ? AND organization_id = ?
                """,
                (str(role), 1 if active else 0, str(member_id), str(organization_id)),
            )
        return self.get_member(member_id) or {}

    def record_login_failure(
        self,
        member_id: str,
        *,
        lock_after: int = 5,
        lock_minutes: int = 15,
    ) -> dict[str, Any]:
        timestamp = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT failed_login_count FROM members WHERE id = ?",
                (str(member_id),),
            ).fetchone()
            current = int(row["failed_login_count"] or 0) if row else 0
            failures = current + 1
            locked_until = (
                datetime.now().timestamp() + lock_minutes * 60 if failures >= lock_after else None
            )
            connection.execute(
                """
                UPDATE members
                SET failed_login_count = ?, locked_until = ?
                WHERE id = ?
                """,
                (failures, locked_until, str(member_id)),
            )
        return {
            "failed_login_count": failures,
            "locked_until": locked_until,
            "updated_at": timestamp,
        }

    def record_login_success(self, member_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE members
                SET failed_login_count = 0, locked_until = NULL
                WHERE id = ?
                """,
                (str(member_id),),
            )

    def set_member_email(
        self,
        member_id: str,
        *,
        email: str,
        verified: bool,
    ) -> dict[str, Any]:
        timestamp = _now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE members
                SET email = ?, email_verified = ?, email_verified_at = ?
                WHERE id = ?
                """,
                (
                    str(email).strip().casefold(),
                    1 if verified else 0,
                    timestamp if verified else None,
                    str(member_id),
                ),
            )
        return self.get_member(member_id) or {}

    def set_member_password(
        self,
        member_id: str,
        *,
        password_hash: str,
        password_salt: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE members
                SET password_hash = ?, password_salt = ?,
                    password_changed_at = ?, failed_login_count = 0,
                    locked_until = NULL
                WHERE id = ?
                """,
                (
                    str(password_hash),
                    str(password_salt),
                    _now(),
                    str(member_id),
                ),
            )

    def create_auth_token(
        self,
        *,
        member_id: str,
        purpose: str,
        destination: str,
        code_hash: str,
        code_salt: str,
        expires_at: float,
        max_attempts: int = 5,
    ) -> dict[str, Any]:
        token_id = f"OTP-{uuid.uuid4().hex[:16].upper()}"
        timestamp = _now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE auth_tokens
                SET used_at = ?
                WHERE member_id = ? AND purpose = ? AND used_at IS NULL
                """,
                (timestamp, str(member_id), str(purpose)),
            )
            connection.execute(
                """
                INSERT INTO auth_tokens
                    (id, member_id, purpose, destination, code_hash, code_salt,
                     expires_at, attempts, max_attempts, used_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, NULL, ?)
                """,
                (
                    token_id,
                    str(member_id),
                    str(purpose),
                    str(destination),
                    str(code_hash),
                    str(code_salt),
                    float(expires_at),
                    int(max_attempts),
                    timestamp,
                ),
            )
        return self.get_auth_token(token_id) or {}

    def get_auth_token(self, token_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM auth_tokens WHERE id = ?",
                (str(token_id),),
            ).fetchone()
        return _row_dict(row)

    def get_active_auth_token(
        self,
        *,
        member_id: str,
        purpose: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM auth_tokens
                WHERE member_id = ? AND purpose = ? AND used_at IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (str(member_id), str(purpose)),
            ).fetchone()
        return _row_dict(row)

    def count_recent_auth_tokens(
        self,
        *,
        member_id: str,
        purpose: str,
        since: datetime,
    ) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM auth_tokens
                WHERE member_id = ? AND purpose = ? AND created_at >= ?
                """,
                (str(member_id), str(purpose), since.isoformat(timespec="seconds")),
            ).fetchone()
        return int(row["total"] or 0) if row else 0

    def increment_auth_token_attempts(self, token_id: str) -> int:
        with self._connect() as connection:
            connection.execute(
                "UPDATE auth_tokens SET attempts = attempts + 1 WHERE id = ?",
                (str(token_id),),
            )
            row = connection.execute(
                "SELECT attempts FROM auth_tokens WHERE id = ?",
                (str(token_id),),
            ).fetchone()
        return int(row["attempts"] or 0) if row else 0

    def mark_auth_token_used(self, token_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE auth_tokens SET used_at = ? WHERE id = ?",
                (_now(), str(token_id)),
            )

    def invalidate_auth_tokens(self, member_id: str, purpose: str | None = None) -> None:
        timestamp = _now()
        with self._connect() as connection:
            if purpose:
                connection.execute(
                    """
                    UPDATE auth_tokens SET used_at = ?
                    WHERE member_id = ? AND purpose = ? AND used_at IS NULL
                    """,
                    (timestamp, str(member_id), str(purpose)),
                )
            else:
                connection.execute(
                    """
                    UPDATE auth_tokens SET used_at = ?
                    WHERE member_id = ? AND used_at IS NULL
                    """,
                    (timestamp, str(member_id)),
                )

    def create_case(
        self,
        *,
        title: str,
        period: str,
        description: str = "",
        status: str = "draft",
        organization_id: str = DEFAULT_ORGANIZATION_ID,
        owner_id: str = DEFAULT_USER_ID,
    ) -> dict[str, Any]:
        clean_title = str(title or "").strip()
        clean_period = str(period or "").strip()
        clean_status = str(status or "draft").strip()
        if not clean_title:
            raise ValueError("审计项目名称不能为空")
        if not clean_period:
            raise ValueError("审计期间不能为空")
        if clean_status not in VALID_CASE_STATUSES:
            raise ValueError("审计项目状态无效")

        case_id = f"CASE-{datetime.now():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"
        timestamp = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_cases
                    (id, organization_id, title, period, description, status,
                     owner_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    str(organization_id),
                    clean_title,
                    clean_period,
                    str(description or "").strip(),
                    clean_status,
                    str(owner_id),
                    timestamp,
                    timestamp,
                ),
            )
        self.log_event(
            action="case.created",
            entity_type="audit_case",
            entity_id=case_id,
            case_id=case_id,
            organization_id=str(organization_id),
            user_id=str(owner_id),
            details={"title": clean_title, "period": clean_period, "status": clean_status},
        )
        return self.get_case(case_id) or {}

    def list_cases(
        self,
        organization_id: str = DEFAULT_ORGANIZATION_ID,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    c.*,
                    m.display_name AS owner_name,
                    (SELECT COUNT(*) FROM case_artifacts a WHERE a.case_id = c.id)
                        AS artifact_count
                FROM audit_cases c
                JOIN members m ON m.id = c.owner_id
                WHERE c.organization_id = ?
                ORDER BY c.updated_at DESC, c.created_at DESC
                """,
                (str(organization_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_case(
        self,
        case_id: str,
        organization_id: str | None = None,
    ) -> dict[str, Any] | None:
        where_organization = ""
        parameters: list[Any] = [str(case_id)]
        if organization_id is not None:
            where_organization = " AND c.organization_id = ?"
            parameters.append(str(organization_id))
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT
                    c.*,
                    m.display_name AS owner_name,
                    (SELECT COUNT(*) FROM case_artifacts a WHERE a.case_id = c.id)
                        AS artifact_count
                FROM audit_cases c
                JOIN members m ON m.id = c.owner_id
                WHERE c.id = ?{where_organization}
                """,
                parameters,
            ).fetchone()
        return _row_dict(row)

    def update_case(
        self,
        case_id: str,
        *,
        status: str | None = None,
        description: str | None = None,
        organization_id: str = DEFAULT_ORGANIZATION_ID,
        user_id: str = DEFAULT_USER_ID,
    ) -> dict[str, Any]:
        existing = self.get_case(case_id, organization_id)
        if existing is None:
            raise WorkspaceStoreError("审计项目不存在")

        updates: list[str] = []
        parameters: list[Any] = []
        if status is not None:
            if status not in VALID_CASE_STATUSES:
                raise ValueError("审计项目状态无效")
            updates.append("status = ?")
            parameters.append(status)
        if description is not None:
            updates.append("description = ?")
            parameters.append(str(description).strip())
        if not updates:
            return existing

        updates.append("updated_at = ?")
        parameters.append(_now())
        parameters.append(str(case_id))
        with self._connect() as connection:
            connection.execute(
                f"UPDATE audit_cases SET {', '.join(updates)} WHERE id = ?",
                parameters,
            )
        self.log_event(
            action="case.updated",
            entity_type="audit_case",
            entity_id=str(case_id),
            case_id=str(case_id),
            organization_id=organization_id,
            user_id=user_id,
            details={
                "status": status if status is not None else existing["status"],
                "description_changed": description is not None,
            },
        )
        return self.get_case(case_id) or {}

    def add_artifact(
        self,
        *,
        case_id: str,
        kind: str,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
        user_id: str = DEFAULT_USER_ID,
    ) -> dict[str, Any]:
        case = self.get_case(case_id)
        if case is None:
            raise WorkspaceStoreError("审计项目不存在")
        clean_filename = Path(str(filename or "")).name.strip()
        if not clean_filename:
            raise ValueError("附件文件名不能为空")
        payload = bytes(content)
        if not payload:
            raise ValueError("附件内容不能为空")
        if len(payload) > MAX_ARTIFACT_BYTES:
            raise ValueError("附件超过 25 MB 安全上限")

        digest = hashlib.sha256(payload).hexdigest()
        artifact_id = f"ART-{uuid.uuid4().hex[:16].upper()}"
        timestamp = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO case_artifacts
                    (id, case_id, kind, filename, content_type, sha256,
                     size_bytes, content, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    str(case_id),
                    str(kind or "evidence").strip(),
                    clean_filename,
                    str(content_type or "application/octet-stream").strip(),
                    digest,
                    len(payload),
                    sqlite3.Binary(payload),
                    str(user_id),
                    timestamp,
                ),
            )
            row = connection.execute(
                """
                SELECT id, case_id, kind, filename, content_type, sha256,
                       size_bytes, created_by, created_at
                FROM case_artifacts
                WHERE case_id = ? AND kind = ? AND sha256 = ?
                """,
                (str(case_id), str(kind or "evidence").strip(), digest),
            ).fetchone()
            connection.execute(
                "UPDATE audit_cases SET updated_at = ? WHERE id = ?",
                (timestamp, str(case_id)),
            )
        if row is None:
            raise WorkspaceStoreError("附件保存失败")
        result = dict(row)
        if result["id"] == artifact_id:
            self.log_event(
                action="artifact.added",
                entity_type="artifact",
                entity_id=artifact_id,
                case_id=str(case_id),
                organization_id=str(case["organization_id"]),
                user_id=str(user_id),
                details={
                    "kind": result["kind"],
                    "filename": result["filename"],
                    "sha256": digest,
                    "size_bytes": len(payload),
                },
            )
        return result

    def list_artifacts(self, case_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, case_id, kind, filename, content_type, sha256,
                       size_bytes, created_by, created_at
                FROM case_artifacts
                WHERE case_id = ?
                ORDER BY created_at DESC
                """,
                (str(case_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM case_artifacts WHERE id = ?",
                (str(artifact_id),),
            ).fetchone()
        return _row_dict(row)

    def create_backup_bytes(self) -> bytes:
        source = sqlite3.connect(str(self.database_path), timeout=20)
        memory = sqlite3.connect(":memory:")
        try:
            source.backup(memory)
            return memory.serialize()
        finally:
            memory.close()
            source.close()

    def database_integrity(self) -> dict[str, Any]:
        with self._connect() as connection:
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            counts = {}
            for table in (
                "organizations",
                "members",
                "audit_cases",
                "case_artifacts",
                "audit_logs",
                "rule_profiles",
                "ai_usage_events",
            ):
                counts[table] = int(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
        return {
            "valid": integrity.casefold() == "ok",
            "integrity": integrity,
            "counts": counts,
        }

    def restore_backup_bytes(self, payload: bytes, *, user_id: str) -> Path:
        backup_data = bytes(payload)
        if not backup_data:
            raise ValueError("备份文件为空")
        temp_path = self.database_path.with_name(f".restore_{uuid.uuid4().hex}.db")
        temp_path.write_bytes(backup_data)
        required = {
            "organizations",
            "members",
            "audit_cases",
            "case_artifacts",
            "audit_logs",
        }
        try:
            source = sqlite3.connect(str(temp_path), timeout=20)
            try:
                integrity = str(source.execute("PRAGMA integrity_check").fetchone()[0])
                tables = {
                    str(row[0])
                    for row in source.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                if integrity.casefold() != "ok" or not required <= tables:
                    raise ValueError("备份文件损坏或缺少必要数据表")
                pre_restore = self.create_backup_bytes()
                backup_path = self.database_path.with_name(
                    f"{self.database_path.stem}_pre_restore_{datetime.now():%Y%m%d_%H%M%S}.db"
                )
                backup_path.write_bytes(pre_restore)
                target = sqlite3.connect(str(self.database_path), timeout=20)
                try:
                    source.backup(target)
                finally:
                    target.close()
            finally:
                source.close()
        finally:
            if temp_path.exists():
                temp_path.unlink()
        self.initialize()
        try:
            self.log_event(
                action="database.restored",
                entity_type="database",
                entity_id=self.database_path.name,
                user_id=user_id,
                details={"pre_restore_backup": backup_path.name},
            )
        except sqlite3.IntegrityError:
            pass
        return backup_path

    def list_rule_profiles(
        self,
        organization_id: str = DEFAULT_ORGANIZATION_ID,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM rule_profiles
                WHERE organization_id = ?
                ORDER BY version DESC
                """,
                (str(organization_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_active_rule_profile(
        self,
        organization_id: str = DEFAULT_ORGANIZATION_ID,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM rule_profiles
                WHERE organization_id = ? AND active = 1
                ORDER BY version DESC
                LIMIT 1
                """,
                (str(organization_id),),
            ).fetchone()
        return _row_dict(row)

    def create_rule_profile(
        self,
        *,
        organization_id: str,
        user_id: str,
        name: str,
        config: dict[str, Any],
        activate: bool = False,
    ) -> dict[str, Any]:
        profile_id = f"RULE-{uuid.uuid4().hex[:16].upper()}"
        with self._connect() as connection:
            version_row = connection.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                FROM rule_profiles
                WHERE organization_id = ?
                """,
                (str(organization_id),),
            ).fetchone()
            version = int(_first_value(version_row))
            if activate:
                connection.execute(
                    "UPDATE rule_profiles SET active = 0 WHERE organization_id = ?",
                    (str(organization_id),),
                )
            connection.execute(
                """
                INSERT INTO rule_profiles
                    (id, organization_id, name, version, config_json,
                     active, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    str(organization_id),
                    str(name).strip(),
                    version,
                    json.dumps(config, ensure_ascii=False, sort_keys=True),
                    1 if activate else 0,
                    str(user_id),
                    _now(),
                ),
            )
        self.log_event(
            action="rules.created",
            entity_type="rule_profile",
            entity_id=profile_id,
            organization_id=organization_id,
            user_id=user_id,
            details={"version": version, "active": activate},
        )
        return self.get_rule_profile(profile_id) or {}

    def get_rule_profile(self, profile_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM rule_profiles WHERE id = ?",
                (str(profile_id),),
            ).fetchone()
        return _row_dict(row)

    def activate_rule_profile(
        self,
        *,
        organization_id: str,
        profile_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        profile = self.get_rule_profile(profile_id)
        if profile is None or profile["organization_id"] != organization_id:
            raise WorkspaceStoreError("规则版本不存在")
        with self._connect() as connection:
            connection.execute(
                "UPDATE rule_profiles SET active = 0 WHERE organization_id = ?",
                (str(organization_id),),
            )
            connection.execute(
                "UPDATE rule_profiles SET active = 1 WHERE id = ?",
                (str(profile_id),),
            )
        self.log_event(
            action="rules.activated",
            entity_type="rule_profile",
            entity_id=str(profile_id),
            organization_id=organization_id,
            user_id=user_id,
            details={"version": profile["version"]},
        )
        return self.get_rule_profile(profile_id) or {}

    def record_ai_usage(
        self,
        *,
        organization_id: str,
        user_id: str,
        module: str,
        model: str,
        input_chars: int,
        redaction_count: int,
        status: str,
        estimated_cost: float = 0.0,
    ) -> int:
        estimated_tokens = max(1, int(input_chars / 2))
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO ai_usage_events
                    (organization_id, user_id, module, model, input_chars,
                     redaction_count, estimated_tokens, estimated_cost,
                     status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    str(organization_id),
                    str(user_id),
                    str(module),
                    str(model),
                    int(input_chars),
                    int(redaction_count),
                    estimated_tokens,
                    float(estimated_cost),
                    str(status),
                    _now(),
                ),
            )
            row = cursor.fetchone()
        return int(row["id"] if isinstance(row, dict) else row[0])

    def ai_usage_summary(
        self,
        organization_id: str = DEFAULT_ORGANIZATION_ID,
        *,
        since: str | None = None,
    ) -> dict[str, Any]:
        sql = """
            SELECT COUNT(*) AS calls, COALESCE(SUM(input_chars),0) AS input_chars,
                   COALESCE(SUM(redaction_count),0) AS redactions,
                   COALESCE(SUM(estimated_tokens),0) AS estimated_tokens,
                   COALESCE(SUM(estimated_cost),0) AS estimated_cost
            FROM ai_usage_events
            WHERE organization_id = ?
        """
        parameters: list[Any] = [str(organization_id)]
        if since:
            sql += " AND created_at >= ?"
            parameters.append(str(since))
        with self._connect() as connection:
            row = connection.execute(sql, parameters).fetchone()
        return dict(row) if row else {}

    def list_ai_usage(
        self,
        organization_id: str = DEFAULT_ORGANIZATION_ID,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM ai_usage_events
                WHERE organization_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (str(organization_id), max(1, min(int(limit), 500))),
            ).fetchall()
        return [dict(row) for row in rows]

    def log_event(
        self,
        *,
        action: str,
        entity_type: str,
        entity_id: str,
        case_id: str | None = None,
        organization_id: str = DEFAULT_ORGANIZATION_ID,
        user_id: str = DEFAULT_USER_ID,
        details: dict[str, Any] | None = None,
    ) -> int:
        details_json = json.dumps(details or {}, ensure_ascii=False, sort_keys=True)
        created_at = _now()
        with self._connect() as connection:
            previous = connection.execute(
                """
                SELECT entry_hash
                FROM audit_logs
                WHERE organization_id = ? AND entry_hash IS NOT NULL
                ORDER BY id DESC
                LIMIT 1
                """,
                (str(organization_id),),
            ).fetchone()
            previous_hash = str(previous["entry_hash"]) if previous else "GENESIS"
            hash_payload = "|".join(
                (
                    previous_hash,
                    str(organization_id),
                    str(case_id) if case_id else "",
                    str(user_id),
                    str(action or "").strip(),
                    str(entity_type or "").strip(),
                    str(entity_id or "").strip(),
                    details_json,
                    created_at,
                )
            )
            entry_hash = hashlib.sha256(hash_payload.encode("utf-8")).hexdigest()
            cursor = connection.execute(
                """
                INSERT INTO audit_logs
                    (organization_id, case_id, user_id, action, entity_type,
                     entity_id, details_json, previous_hash, entry_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    str(organization_id),
                    str(case_id) if case_id else None,
                    str(user_id),
                    str(action or "").strip(),
                    str(entity_type or "").strip(),
                    str(entity_id or "").strip(),
                    details_json,
                    previous_hash,
                    entry_hash,
                    created_at,
                ),
            )
            row = cursor.fetchone()
        return int(row["id"] if isinstance(row, dict) else row[0])

    def verify_audit_chain(
        self,
        organization_id: str = DEFAULT_ORGANIZATION_ID,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM audit_logs
                WHERE organization_id = ?
                ORDER BY id ASC
                """,
                (str(organization_id),),
            ).fetchall()

        expected_previous: str | None = None
        checked = 0
        legacy = 0
        for row in rows:
            entry_hash = row["entry_hash"]
            if not entry_hash:
                legacy += 1
                continue
            if expected_previous is not None and row["previous_hash"] != expected_previous:
                return {
                    "valid": False,
                    "checked": checked,
                    "legacy": legacy,
                    "broken_id": int(row["id"]),
                    "reason": "前序哈希不匹配",
                }
            payload = "|".join(
                (
                    str(row["previous_hash"] or ""),
                    str(row["organization_id"]),
                    str(row["case_id"] or ""),
                    str(row["user_id"]),
                    str(row["action"]),
                    str(row["entity_type"]),
                    str(row["entity_id"]),
                    str(row["details_json"]),
                    str(row["created_at"]),
                )
            )
            calculated = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            if calculated != entry_hash:
                return {
                    "valid": False,
                    "checked": checked,
                    "legacy": legacy,
                    "broken_id": int(row["id"]),
                    "reason": "日志内容哈希不匹配",
                }
            expected_previous = str(entry_hash)
            checked += 1
        return {
            "valid": True,
            "checked": checked,
            "legacy": legacy,
            "broken_id": None,
            "reason": "",
        }

    def list_logs(
        self,
        *,
        case_id: str | None = None,
        organization_id: str = DEFAULT_ORGANIZATION_ID,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT l.*, m.display_name AS user_name
            FROM audit_logs l
            JOIN members m ON m.id = l.user_id
            WHERE l.organization_id = ?
        """
        parameters: list[Any] = [str(organization_id)]
        if case_id:
            sql += " AND l.case_id = ?"
            parameters.append(str(case_id))
        sql += " ORDER BY l.id DESC LIMIT ?"
        parameters.append(max(1, min(int(limit), 500)))
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("details_json", "{}"))
            except json.JSONDecodeError:
                item["details"] = {}
            output.append(item)
        return output
