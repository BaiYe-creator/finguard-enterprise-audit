"""Isolated local anomaly detection and optional financial-text analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
import streamlit as st

try:
    import numpy as np
    from sklearn.ensemble import IsolationForest

    ML_AVAILABLE = True
    ML_DEPENDENCY_ERROR = ""
except Exception as exc:  # pragma: no cover - exercised only in incomplete environments
    np = None
    IsolationForest = None
    ML_AVAILABLE = False
    ML_DEPENDENCY_ERROR = str(exc)


class MLDependencyError(RuntimeError):
    """Raised when the optional machine-learning runtime is unavailable."""


@dataclass(frozen=True)
class FinancialNarrativeResult:
    status: str
    content: str


def ml_dependency_available() -> bool:
    return ML_AVAILABLE


@st.cache_data(show_spinner=False)
def detect_amount_anomalies(
    amounts: pd.Series,
    ledger_hash: str,
) -> pd.DataFrame:
    """Detect amount outliers with a cached Isolation Forest model."""

    if not ML_AVAILABLE or np is None or IsolationForest is None:
        raise MLDependencyError("缺少机器学习依赖，请安装")

    numeric = pd.to_numeric(amounts, errors="coerce")
    valid = numeric.dropna()
    if valid.empty:
        return pd.DataFrame(columns=["序号", "金额", "预测值", "异常类型", "是否异常", "账本哈希"])

    values = np.array(valid, dtype=float).reshape(-1, 1)
    model = IsolationForest(contamination=0.05, random_state=42)
    predictions = model.fit_predict(values)
    return pd.DataFrame(
        {
            "序号": list(range(1, len(valid) + 1)),
            "金额": valid.to_numpy(dtype=float),
            "预测值": predictions,
            "异常类型": ["异常" if value == -1 else "正常" for value in predictions],
            "是否异常": predictions == -1,
            "账本哈希": [ledger_hash] * len(valid),
        }
    )


def analyze_financial_summary(
    text: str,
    *,
    api_key: str,
    model: str = "deepseek-chat",
    client: Any | None = None,
) -> FinancialNarrativeResult:
    """Analyze a financial narrative with a strict timeout and safe fallback."""

    summary = str(text or "").strip()
    if not summary:
        return FinancialNarrativeResult("invalid", "请先输入财报摘要")

    active_client = client
    try:
        if active_client is None:
            if not api_key.strip():
                return FinancialNarrativeResult("disabled", "未配置 AI 密钥")
            from openai import OpenAI

            active_client = OpenAI(
                api_key=api_key,
                base_url="https://api.deepseek.com",
                timeout=15.0,
                max_retries=0,
            )
        response = active_client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个顶尖的财务造假调查员。分析这段文本，如果发现管理层"
                        "过度自信、闪烁其词、或大量套话掩盖负面信息，输出'高造假嫌疑'及理由。"
                    ),
                },
                {"role": "user", "content": summary[:6000]},
            ],
            max_tokens=500,
            stream=False,
            timeout=15.0,
        )
        choices = getattr(response, "choices", None)
        content = getattr(getattr(choices[0], "message", None), "content", "") if choices else ""
        if not isinstance(content, str) or not content.strip():
            return FinancialNarrativeResult("error", "AI分析暂时不可用")
        return FinancialNarrativeResult("success", content.strip())
    except Exception as exc:
        error_name = type(exc).__name__.casefold()
        if "timeout" in error_name:
            return FinancialNarrativeResult("timeout", "AI分析超时")
        return FinancialNarrativeResult("error", "AI分析暂时不可用")
