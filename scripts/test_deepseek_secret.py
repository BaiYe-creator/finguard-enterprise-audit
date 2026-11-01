"""Masked DeepSeek secret-path and live-request diagnostic."""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Any

import pandas as pd
from streamlit.runtime.secrets import Secrets

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SECRETS_PATH = PROJECT_ROOT / ".streamlit" / "secrets.toml"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _mask(value: str) -> str:
    if not value:
        return "(empty)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * min(16, len(value) - 8)}{value[-4:]}"


def _read_toml_secret() -> tuple[str, str]:
    if not SECRETS_PATH.exists():
        return "", "secrets.toml 不存在"
    with SECRETS_PATH.open("rb") as handle:
        config = tomllib.load(handle)
    return str(config.get("DEEPSEEK_API_KEY", "")).strip(), "toml"


def _read_streamlit_secret() -> tuple[str, str]:
    try:
        manager = Secrets()
        manager.load_if_toml_exists()
        value = str(manager["DEEPSEEK_API_KEY"]).strip()
        return value, "st.secrets"
    except Exception as exc:
        return "", f"st.secrets 读取失败：{type(exc).__name__}"


def main() -> int:
    from finguard.ai_auditor import AIAuditor

    os.chdir(PROJECT_ROOT)
    toml_key, toml_source = _read_toml_secret()
    streamlit_key, streamlit_source = _read_streamlit_secret()
    key = streamlit_key or toml_key
    source = streamlit_source if streamlit_key else toml_source

    print(f"project_root={PROJECT_ROOT}")
    print(f"secrets_path={SECRETS_PATH}")
    print(f"secrets_exists={SECRETS_PATH.exists()}")
    print(f"key_present={bool(key)}")
    print(f"key_source={source}")
    print(f"key_length={len(key)}")
    print(f"key_masked={_mask(key)}")
    print(f"toml_matches_streamlit={bool(toml_key) and toml_key == streamlit_key}")

    if not key or key.casefold().startswith(("paste-", "replace-")):
        print("result=SKIPPED_NO_REAL_KEY")
        return 2

    row: dict[str, Any] = {
        "voucher_id": "AI-SECRET-CHECK",
        "date": pd.Timestamp("2026-10-29 02:15:00"),
        "user": "脱敏测试员",
        "summary": "办公用品一批，缺少明细",
        "amount": 128_000.0,
        "Risk_Score": 100,
        "Risk_Features": "金额极值；凌晨录入；模糊摘要",
    }
    try:
        opinion = AIAuditor(api_key=key, model="deepseek-chat").audit_voucher(row)
    except Exception as exc:
        print(f"api_result=FAILED error_type={type(exc).__name__}")
        print(f"api_error={str(exc)[:300]}")
        return 1

    print("api_result=OK")
    print("ai_advice_preview=" + " ".join(opinion.split())[:500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
