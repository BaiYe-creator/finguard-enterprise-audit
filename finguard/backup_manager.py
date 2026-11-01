"""Automatic SQLite snapshots with hash verification and retention."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from finguard.postgres_store import BACKUP_MARKER, PG_BIN, PostgresWorkspaceStore
from finguard.workspace_store import WorkspaceStore


@dataclass(frozen=True)
class BackupInfo:
    path: str
    filename: str
    size_bytes: int
    sha256: str
    created_at: str
    valid: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BackupManager:
    def __init__(
        self,
        store: WorkspaceStore,
        backup_dir: str | Path,
        *,
        retention: int = 14,
    ) -> None:
        self.store = store
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.retention = max(1, int(retention))
        self.backend_tag = (
            "supabase"
            if isinstance(store, PostgresWorkspaceStore)
            and "pooler.supabase.com" in store.database_url
            else "postgres"
            if isinstance(store, PostgresWorkspaceStore)
            else "sqlite"
        )

    def ensure_daily_backup(self) -> BackupInfo:
        today = datetime.now().strftime("%Y%m%d")
        extension = "dump" if isinstance(self.store, PostgresWorkspaceStore) else "db"
        existing = sorted(
            self.backup_dir.glob(f"FinGuard_{self.backend_tag}_auto_{today}_*.{extension}")
        )
        for candidate in reversed(existing):
            info = self.inspect(candidate)
            if info.valid:
                return info
            candidate.unlink(missing_ok=True)
            candidate.with_suffix(".sha256").unlink(missing_ok=True)
        return self.create_backup(label="auto")

    def create_backup(self, *, label: str = "auto") -> BackupInfo:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = uuid.uuid4().hex[:6]
        extension = "dump" if isinstance(self.store, PostgresWorkspaceStore) else "db"
        filename = f"FinGuard_{self.backend_tag}_{label}_{timestamp}_{suffix}.{extension}"
        path = self.backup_dir / filename
        payload = self.store.create_backup_bytes()
        path.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        path.with_suffix(".sha256").write_text(digest, encoding="ascii")
        self._prune()
        return self.inspect(path)

    def inspect(self, path: str | Path) -> BackupInfo:
        backup_path = Path(path)
        payload = backup_path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        sidecar = backup_path.with_suffix(".sha256")
        recorded_digest = sidecar.read_text(encoding="ascii").strip() if sidecar.exists() else ""
        valid = bool(recorded_digest and digest == recorded_digest)
        if valid and payload.startswith(BACKUP_MARKER):
            try:
                data = json.loads(payload[len(BACKUP_MARKER) :].decode("utf-8"))
                valid = data.get("format") == "finguard-postgres-json"
            except Exception:
                valid = False
        elif valid and isinstance(self.store, PostgresWorkspaceStore):
            try:
                result = subprocess.run(
                    [str(PG_BIN / "pg_restore.exe"), "--list", str(backup_path)],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                valid = result.returncode == 0
            except Exception:
                valid = False
        elif valid:
            try:
                connection = sqlite3.connect(str(backup_path), timeout=10)
                try:
                    integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
                finally:
                    connection.close()
                valid = integrity.casefold() == "ok"
            except sqlite3.Error:
                valid = False
        return BackupInfo(
            path=str(backup_path),
            filename=backup_path.name,
            size_bytes=len(payload),
            sha256=digest,
            created_at=datetime.fromtimestamp(backup_path.stat().st_mtime).isoformat(
                timespec="seconds"
            ),
            valid=valid,
        )

    def list_backups(self) -> list[BackupInfo]:
        candidates = [
            *self.backup_dir.glob("FinGuard_*.db"),
            *self.backup_dir.glob("FinGuard_*.dump"),
        ]
        return [
            self.inspect(path)
            for path in sorted(
                candidates,
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        ]

    def _prune(self) -> None:
        candidates = [
            *self.backup_dir.glob("FinGuard_*.db"),
            *self.backup_dir.glob("FinGuard_*.dump"),
        ]
        backups = sorted(
            candidates,
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for expired in backups[self.retention :]:
            expired.unlink(missing_ok=True)
            expired.with_suffix(".sha256").unlink(missing_ok=True)
