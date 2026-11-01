"""Local anomaly detection and AI-assisted financial narrative page."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

import pandas as pd
import streamlit as st

from finguard.ml_risk_engine import (
    MLDependencyError,
    analyze_financial_summary,
    detect_amount_anomalies,
    ml_dependency_available,
)

PageHeader = Callable[[str, str, str], None]
TextTransform = Callable[[str], tuple[str, int]]
AnalysisCallback = Callable[[str, int, str], None]
BudgetCheck = Callable[[], tuple[bool, str]]


def _current_ml_amounts() -> tuple[pd.Series, str]:
    editable_entries = st.session_state.get("v04_editable_entries", [])
    if editable_entries:
        return (
            pd.Series(
                [
                    entry.get("amount", 0)
                    for entry in editable_entries
                    if entry.get("status") == "generated"
                ],
                dtype="float64",
            ),
            "V0.4 人工复核账本",
        )

    analysis = st.session_state.get("analysis")
    if analysis and isinstance(analysis.get("data"), pd.DataFrame):
        return (
            pd.to_numeric(analysis["data"].get("amount"), errors="coerce"),
            "财务审计账本",
        )

    business_history = st.session_state.get("biz_history", [])
    business_amounts = [
        entry.get("amount", 0) for entry in business_history if entry.get("status") == "generated"
    ]
    return pd.Series(business_amounts, dtype="float64"), "业财转换分录"


def render_intelligent_page(
    render_header: PageHeader,
    *,
    deepseek_key: Callable[[], str],
    model: str,
    text_transform: TextTransform | None = None,
    analysis_callback: AnalysisCallback | None = None,
    budget_check: BudgetCheck | None = None,
) -> None:
    render_header(
        "智能化引擎",
        "INTELLIGENT RISK",
        "back_from_mlrisk",
    )
    st.caption("孤立森林检测金额离群点，财报摘要分析仅在用户点击后调用模型。")
    left, right = st.columns([1.25, 0.75], gap="large")

    with left:
        st.markdown("### 金额异常检测")
        if not ml_dependency_available():
            st.error("缺少机器学习依赖，请安装")
        else:
            amounts, source = _current_ml_amounts()
            if amounts.empty:
                st.info("当前没有可用账本。请先在财务审计或业财转换中载入数据。")
            else:
                ledger_hash = hashlib.sha256(
                    pd.util.hash_pandas_object(amounts, index=False).values.tobytes()
                ).hexdigest()
                try:
                    anomaly_frame = detect_amount_anomalies(amounts, ledger_hash)
                except MLDependencyError:
                    st.error("缺少机器学习依赖，请安装")
                except Exception:
                    st.error("金额异常检测暂时不可用")
                else:
                    st.session_state.v05_ml_anomaly_frame = anomaly_frame
                    anomaly_count = int(anomaly_frame["是否异常"].sum())
                    st.caption(f"数据来源：{source}，共 {len(anomaly_frame):,} 笔金额")
                    if anomaly_frame.empty:
                        st.info("当前金额数据不足，无法完成异常检测。")
                    else:
                        chart_data = pd.DataFrame(
                            {
                                "序号": anomaly_frame["序号"],
                                "正常金额": anomaly_frame["金额"].where(~anomaly_frame["是否异常"]),
                                "异常金额": anomaly_frame["金额"].where(anomaly_frame["是否异常"]),
                            }
                        )
                        st.scatter_chart(
                            chart_data,
                            x="序号",
                            y=["正常金额", "异常金额"],
                            color=["#B8BEC5", "#C0392B"],
                            height=360,
                        )
                        st.caption(
                            f"红色为孤立森林识别出的异常点，共 {anomaly_count:,} 笔；"
                            "灰色为常规金额。"
                        )

    with right:
        st.markdown("### 财报摘要分析")
        summary = st.text_area(
            "财报摘要",
            height=220,
            key="v05_ml_summary_text",
            placeholder="输入管理层讨论、业绩说明或公告摘要。",
        )
        analyze_clicked = st.button(
            "分析财报摘要",
            type="primary",
            width="stretch",
        )
        if analyze_clicked:
            allowed, budget_message = budget_check() if budget_check else (True, "")
            if not allowed:
                st.warning(budget_message)
            else:
                prepared_summary, redaction_count = (
                    text_transform(summary) if text_transform is not None else (summary, 0)
                )
                with st.spinner("正在进行财报摘要分析"):
                    result = analyze_financial_summary(
                        prepared_summary,
                        api_key=deepseek_key(),
                        model=model,
                    )
                if analysis_callback is not None:
                    analysis_callback(prepared_summary, redaction_count, result.status)
                st.session_state.v05_ml_nlp_result = {
                    "status": result.status,
                    "content": result.content,
                }

        narrative = st.session_state.get("v05_ml_nlp_result")
        if narrative:
            if narrative["status"] == "success":
                st.markdown(narrative["content"])
            elif narrative["status"] == "timeout":
                st.caption("AI分析超时")
            elif narrative["status"] == "disabled":
                st.caption("未配置 AI 密钥")
            else:
                st.caption(narrative["content"])
