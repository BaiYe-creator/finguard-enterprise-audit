"""Migrate the local FinGuard SQLite database into PostgreSQL."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import psycopg

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from finguard.postgres_store import PostgresWorkspaceStore  # noqa: E402

TABLES = (
    "organizations",
    "members",
    "audit_cases",
    "case_artifacts",
    "audit_logs",
    "auth_tokens",
    "rule_profiles",
    "ai_usage_events",
)
COLUMNS = {
    "organizations": ("id", "name", "slug", "created_at"),
    "members": (
        "id",
        "organization_id",
        "display_name",
        "role",
        "username",
        "password_hash",
        "password_salt",
        "email",
        "email_verified",
        "email_verified_at",
        "password_changed_at",
        "failed_login_count",
        "locked_until",
        "active",
        "created_at",
    ),
    "audit_cases": (
        "id",
        "organization_id",
        "title",
        "period",
        "description",
        "status",
        "owner_id",
        "created_at",
        "updated_at",
    ),
    "case_artifacts": (
        "id",
        "case_id",
        "kind",
        "filename",
        "content_type",
        "sha256",
        "size_bytes",
        "content",
        "created_by",
        "created_at",
    ),
    "audit_logs": (
        "id",
        "organization_id",
        "case_id",
        "user_id",
        "action",
        "entity_type",
        "entity_id",
        "details_json",
        "previous_hash",
        "entry_hash",
        "created_at",
    ),
    "auth_tokens": (
        "id",
        "member_id",
        "purpose",
        "destination",
        "code_hash",
        "code_salt",
        "expires_at",
        "attempts",
        "max_attempts",
        "used_at",
        "created_at",
    ),
    "rule_profiles": (
        "id",
        "organization_id",
        "name",
        "version",
        "config_json",
        "active",
        "created_by",
        "created_at",
    ),
    "ai_usage_events": (
        "id",
        "organization_id",
        "user_id",
        "module",
        "model",
        "input_chars",
        "redaction_count",
        "estimated_tokens",
        "estimated_cost",
        "status",
        "created_at",
    ),
}


def migrate(sqlite_path: Path, database_url: str) -> dict[str, int]:
    if not sqlite_path.exists():
        raise FileNotFoundError(sqlite_path)
    PostgresWorkspaceStore(database_url)
    source = sqlite3.connect(str(sqlite_path), timeout=20)
    source.row_factory = sqlite3.Row
    counts: dict[str, int] = {}
    try:
        with psycopg.connect(database_url, connect_timeout=20) as target:
            target.execute(
                "TRUNCATE TABLE " + ", ".join(reversed(TABLES)) + " RESTART IDENTITY CASCADE"
            )
            for table in TABLES:
                columns = COLUMNS[table]
                rows = source.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
                if rows:
                    placeholders = ", ".join(["%s"] * len(columns))
                    with target.cursor() as cursor:
                        cursor.executemany(
                            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                            [tuple(row[column] for column in columns) for row in rows],
                        )
                counts[table] = len(rows)
            target.execute(
                """
                SELECT setval(
                    pg_get_serial_sequence('audit_logs', 'id'),
                    COALESCE((SELECT MAX(id) FROM audit_logs), 1),
                    EXISTS(SELECT 1 FROM audit_logs)
                )
                """
            )
            target.execute(
                """
                SELECT setval(
                    pg_get_serial_sequence('ai_usage_events', 'id'),
                    COALESCE((SELECT MAX(id) FROM ai_usage_events), 1),
                    EXISTS(SELECT 1 FROM ai_usage_events)
                )
                """
            )
    finally:
        source.close()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=PROJECT_ROOT / "data" / "finguard.db",
    )
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    counts = migrate(args.sqlite, args.database_url)
    print("migration_passed", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
