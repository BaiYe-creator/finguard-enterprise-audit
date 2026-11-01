from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from finguard.ml_risk_engine import (
    ML_AVAILABLE,
    analyze_financial_summary,
    detect_amount_anomalies,
    ml_dependency_available,
)


def test_isolation_forest_returns_anomaly_labels_and_uses_cache() -> None:
    amounts = pd.Series([100.0] * 40 + [5000.0] * 4 + [100000.0])
    dataframe = detect_amount_anomalies(amounts, "ledger-hash-1")

    assert ml_dependency_available() is ML_AVAILABLE
    assert len(dataframe) == len(amounts)
    assert set(dataframe["预测值"].unique()).issubset({-1, 1})
    assert dataframe["是否异常"].any()
    assert callable(detect_amount_anomalies.clear)


def test_empty_amount_series_returns_empty_result() -> None:
    dataframe = detect_amount_anomalies(pd.Series([], dtype=float), "empty")

    assert dataframe.empty
    assert list(dataframe.columns) == [
        "序号",
        "金额",
        "预测值",
        "异常类型",
        "是否异常",
        "账本哈希",
    ]


class SuccessCompletions:
    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="高造假嫌疑。摘要存在模糊表述，建议核实现金流与收入确认依据。"
                    )
                )
            ]
        )


def test_narrative_analysis_uses_timeout_and_returns_ai_text() -> None:
    completions = SuccessCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    result = analyze_financial_summary(
        "公司经营稳定，风险完全可控。",
        api_key="test-key",
        client=client,
    )

    assert result.status == "success"
    assert "高造假嫌疑" in result.content
    assert completions.kwargs["timeout"] == 15.0
    assert "顶尖的财务造假调查员" in completions.kwargs["messages"][0]["content"]


def test_narrative_timeout_returns_safe_gray_fallback() -> None:
    class APITimeoutError(Exception):
        pass

    class TimeoutCompletions:
        def create(self, **kwargs):
            raise APITimeoutError("timed out")

    client = SimpleNamespace(chat=SimpleNamespace(completions=TimeoutCompletions()))
    result = analyze_financial_summary(
        "文本",
        api_key="test-key",
        client=client,
    )

    assert result.status == "timeout"
    assert result.content == "AI分析超时"


def test_missing_api_key_is_non_crashing() -> None:
    result = analyze_financial_summary("文本", api_key="")

    assert result.status == "disabled"
    assert result.content == "未配置 AI 密钥"
