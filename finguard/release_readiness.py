"""Release blockers and production-readiness checks."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tomllib
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from finguard.version import __version__

HARDCODED_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
)


@dataclass(frozen=True)
class ReleaseCheck:
    name: str
    status: str
    detail: str
    blocking: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_secrets(project_root: Path) -> dict[str, Any]:
    path = project_root / ".streamlit" / "secrets.toml"
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_bytes().decode("utf-8-sig"))
    except Exception:
        return {"_parse_error": True}


def _truthy(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _scan_hardcoded_secrets(project_root: Path) -> list[str]:
    findings: list[str] = []
    for path in project_root.rglob("*.py"):
        if any(part in {".venv", "__pycache__", ".git"} for part in path.parts):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in HARDCODED_SECRET_PATTERNS:
            if pattern.search(content):
                findings.append(str(path.relative_to(project_root)))
                break
    return findings


def _database_check(
    project_root: Path,
    secrets: dict[str, Any],
    env: dict[str, str],
) -> ReleaseCheck:
    use_postgres = env.get("FINGUARD_USE_POSTGRES") or secrets.get(
        "FINGUARD_USE_POSTGRES",
        False,
    )
    database_url = env.get("FINGUARD_DATABASE_URL") or secrets.get(
        "FINGUARD_DATABASE_URL",
        "",
    )
    if _truthy(use_postgres) and database_url:
        try:
            from finguard.postgres_store import PostgresWorkspaceStore

            integrity = PostgresWorkspaceStore(str(database_url)).database_integrity()
            return ReleaseCheck(
                "数据库完整性",
                "PASS" if integrity["valid"] else "FAIL",
                "PostgreSQL integrity=ok",
                not integrity["valid"],
            )
        except Exception as exc:
            return ReleaseCheck(
                "数据库完整性",
                "FAIL",
                f"PostgreSQL 检查失败：{type(exc).__name__}",
                True,
            )
    database_value = (
        env.get("FINGUARD_DB_PATH")
        or secrets.get("FINGUARD_DB_PATH")
        or str(project_root / "data" / "finguard.db")
    )
    path = Path(str(database_value))
    if not path.exists():
        return ReleaseCheck(
            "数据库完整性",
            "FAIL",
            f"数据库不存在：{path}",
            True,
        )
    try:
        connection = sqlite3.connect(str(path), timeout=10)
        try:
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return ReleaseCheck("数据库完整性", "FAIL", str(exc), True)
    required = {"organizations", "members", "audit_cases", "audit_logs"}
    if integrity.casefold() != "ok" or not required <= tables:
        return ReleaseCheck(
            "数据库完整性",
            "FAIL",
            f"integrity={integrity}，缺少表={sorted(required - tables)}",
            True,
        )
    return ReleaseCheck("数据库完整性", "PASS", f"SQLite integrity=ok · {path.name}", False)


def run_release_checks(
    project_root: str | Path,
    *,
    environment: dict[str, str] | None = None,
) -> list[ReleaseCheck]:
    root = Path(project_root).resolve()
    env = dict(os.environ) if environment is None else dict(environment)
    secrets = _load_secrets(root)
    checks: list[ReleaseCheck] = [
        ReleaseCheck("版本标识", "PASS", f"V{__version__}", False),
    ]

    if secrets.get("_parse_error"):
        checks.append(ReleaseCheck("Secrets 格式", "FAIL", "secrets.toml 无法解析", True))
    else:
        checks.append(ReleaseCheck("Secrets 格式", "PASS", "secrets.toml 可解析或未提供", False))

    auth_required = env.get("FINGUARD_AUTH_REQUIRED") or secrets.get(
        "FINGUARD_AUTH_REQUIRED",
        False,
    )
    checks.append(
        ReleaseCheck(
            "强制登录",
            "PASS" if _truthy(auth_required) else "FAIL",
            "FINGUARD_AUTH_REQUIRED 已开启" if _truthy(auth_required) else "未开启强制登录",
            not _truthy(auth_required),
        )
    )

    dev_mode = env.get("FINGUARD_EMAIL_DEV_MODE") or secrets.get(
        "FINGUARD_EMAIL_DEV_MODE",
        False,
    )
    checks.append(
        ReleaseCheck(
            "邮件测试模式",
            "FAIL" if _truthy(dev_mode) else "PASS",
            "开发验证码模式仍开启" if _truthy(dev_mode) else "未开启开发验证码模式",
            _truthy(dev_mode),
        )
    )

    smtp_required = ("SMTP_HOST", "SMTP_USER", "SMTP_AUTH_CODE")
    smtp_ready = all(bool(secrets.get(key)) for key in smtp_required)
    checks.append(
        ReleaseCheck(
            "找回密码邮件",
            "PASS" if smtp_ready else "FAIL",
            "SMTP 配置完整" if smtp_ready else "SMTP 配置不完整",
            not smtp_ready,
        )
    )

    secret_files = _scan_hardcoded_secrets(root)
    checks.append(
        ReleaseCheck(
            "硬编码密钥扫描",
            "FAIL" if secret_files else "PASS",
            f"发现疑似密钥：{', '.join(secret_files)}"
            if secret_files
            else "未发现明显硬编码 API Key",
            bool(secret_files),
        )
    )

    checks.append(_database_check(root, secrets, env))

    ignore_path = root / ".gitignore"
    ignore_text = ignore_path.read_text(encoding="utf-8") if ignore_path.exists() else ""
    secrets_ignored = ".streamlit/secrets.toml" in ignore_text
    checks.append(
        ReleaseCheck(
            "Secrets 忽略规则",
            "PASS" if secrets_ignored else "FAIL",
            ".gitignore 已排除 secrets.toml"
            if secrets_ignored
            else ".gitignore 未排除 secrets.toml",
            not secrets_ignored,
        )
    )

    backup_dir = root / "data" / "backups"
    backups = (
        sorted([*backup_dir.glob("*.db"), *backup_dir.glob("*.dump")])
        if backup_dir.exists()
        else []
    )
    checks.append(
        ReleaseCheck(
            "本地备份存在",
            "PASS" if backups else "WARN",
            f"发现 {len(backups)} 份备份" if backups else "尚未发现自动备份文件",
            False,
        )
    )

    postgres_enabled = _truthy(
        env.get("FINGUARD_USE_POSTGRES") or secrets.get("FINGUARD_USE_POSTGRES", False)
    )
    postgres_configured = bool(
        env.get("FINGUARD_DATABASE_URL") or secrets.get("FINGUARD_DATABASE_URL")
    )
    checks.append(
        ReleaseCheck(
            "PostgreSQL 外部数据库",
            "PASS" if postgres_enabled and postgres_configured else "WARN",
            "已启用并配置 PostgreSQL"
            if postgres_enabled and postgres_configured
            else "当前仍使用本地 SQLite，V1.0 公测前必须切换并完成恢复演练",
            False,
        )
    )
    e2e_marker = root / "data" / "browser_e2e_last_success.json"
    e2e_recent = False
    e2e_detail = "仍需在真实浏览器执行注册、登录、审计、导出和恢复流程"
    if e2e_marker.exists():
        try:
            marker = json.loads(e2e_marker.read_text(encoding="utf-8-sig"))
            tested_at = datetime.fromisoformat(str(marker["tested_at"]))
            age_hours = (datetime.now(tested_at.tzinfo) - tested_at).total_seconds() / 3600
            e2e_recent = marker.get("status") == "success" and age_hours <= 168
            e2e_detail = (
                f"最近浏览器验收：{tested_at.isoformat(timespec='seconds')}"
                if e2e_recent
                else "浏览器验收记录已过期"
            )
        except Exception:
            e2e_detail = "浏览器验收标记无法解析"
    checks.append(
        ReleaseCheck(
            "浏览器 E2E",
            "PASS" if e2e_recent else "WARN",
            e2e_detail,
            False,
        )
    )
    return checks


def summary(checks: list[ReleaseCheck]) -> dict[str, int]:
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    counts["blocking"] = sum(1 for check in checks if check.blocking)
    return counts
