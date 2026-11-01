from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from finguard.backup_manager import BackupManager
from finguard.workspace_store import WorkspaceStore


def test_daily_backup_is_reused_and_verified() -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        root = Path(directory)
        store = WorkspaceStore(root / "data" / "finguard.db")
        store.create_case(title="自动备份项目", period="2026-11")
        manager = BackupManager(store, root / "data" / "backups")

        first = manager.ensure_daily_backup()
        second = manager.ensure_daily_backup()
        assert first.path == second.path
        assert first.valid
        assert Path(first.path).exists()
        assert Path(first.path).with_suffix(".sha256").exists()


def test_backup_retention_and_tamper_detection() -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        root = Path(directory)
        manager = BackupManager(
            WorkspaceStore(root / "data" / "finguard.db"),
            root / "data" / "backups",
            retention=2,
        )
        first = manager.create_backup(label="manual")
        manager.create_backup(label="manual")
        latest = manager.create_backup(label="manual")
        assert len(manager.list_backups()) == 2
        assert not Path(first.path).exists()

        path = Path(latest.path)
        path.write_bytes(path.read_bytes() + b"tampered")
        assert not manager.inspect(path).valid
