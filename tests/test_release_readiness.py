from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from finguard.release_readiness import run_release_checks, summary
from finguard.workspace_store import WorkspaceStore


def _project(directory: Path) -> Path:
    root = directory / "project"
    (root / ".streamlit").mkdir(parents=True)
    (root / ".gitignore").write_text(".streamlit/secrets.toml\n", encoding="utf-8")
    secrets = root / ".streamlit" / "secrets.toml"
    secrets.write_text(
        "FINGUARD_AUTH_REQUIRED = true\n"
        'SMTP_HOST = "smtp.qq.com"\n'
        'SMTP_USER = "user@example.com"\n'
        'SMTP_AUTH_CODE = "test-auth-code"\n',
        encoding="utf-8",
    )
    WorkspaceStore(root / "data" / "finguard.db")
    return root


def test_release_gate_passes_non_blocking_checks_without_secret_findings() -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        root = _project(Path(directory))
        checks = run_release_checks(root, environment={})
        result = summary(checks)
        assert result["blocking"] == 0
        assert result["PASS"] >= 5
        assert any(check.name == "PostgreSQL 外部数据库" for check in checks)


def test_release_gate_blocks_dev_mode_and_hardcoded_secret() -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        root = _project(Path(directory))
        secrets = root / ".streamlit" / "secrets.toml"
        secrets.write_text(
            secrets.read_text(encoding="utf-8") + "FINGUARD_EMAIL_DEV_MODE = true\n",
            encoding="utf-8",
        )
        (root / "bad.py").write_text(
            'API_KEY = "' + "sk-" + 'abcdefghijklmnopqrstuvwxyz123456"\n',
            encoding="utf-8",
        )
        checks = run_release_checks(root, environment={})
        failed = {check.name for check in checks if check.status == "FAIL"}
        assert "邮件测试模式" in failed
        assert "硬编码密钥扫描" in failed
        assert summary(checks)["blocking"] >= 2
