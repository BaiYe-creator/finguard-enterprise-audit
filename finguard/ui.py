"""Streamlit rendering helpers for the FinGuard dashboard."""

from __future__ import annotations

import html
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from finguard.version import version_label

PRIMARY = "#20242A"
TEAL = "#2B7A78"
RED = "#8B1E2D"
YELLOW = "#C89A24"
MUTED = "#6B7280"


def format_cny(value: float) -> str:
    absolute = abs(float(value))
    if absolute >= 100_000_000:
        return f"¥{value / 100_000_000:,.2f} 亿"
    if absolute >= 10_000:
        return f"¥{value / 10_000:,.2f} 万"
    return f"¥{value:,.2f}"


class FinGuardUI:
    """Commercial audit workspace rendering."""

    def inject_styles(self) -> None:
        st.markdown(
            """
            <style>
            :root {
                --fg-navy: #102A43;
                --fg-teal: #167D7F;
                --fg-red: #8B1E2D;
                --fg-mist: #F4F7FA;
            }
            .stApp {
                background:
                    radial-gradient(circle at 96% 2%, rgba(22,125,127,.10), transparent 27rem),
                    linear-gradient(180deg, #F8FAFC 0%, #FFFFFF 32rem);
            }
            .block-container {
                padding-top: 1.5rem;
                padding-bottom: 4rem;
                max-width: 1500px;
            }
            .fg-hero {
                position: relative;
                overflow: hidden;
                padding: 1.8rem 2rem 1.7rem;
                border-radius: 20px;
                color: #fff;
                background: linear-gradient(125deg, #102A43 0%, #173E59 58%, #167D7F 130%);
                box-shadow: 0 18px 50px rgba(16,42,67,.16);
                margin-bottom: 1.25rem;
            }
            .fg-hero:after {
                content: "";
                position: absolute;
                width: 20rem;
                height: 20rem;
                border: 1px solid rgba(255,255,255,.16);
                border-radius: 50%;
                right: -5rem;
                top: -8rem;
                box-shadow: 0 0 0 3rem rgba(255,255,255,.035),
                            0 0 0 6rem rgba(255,255,255,.025);
            }
            .fg-eyebrow {
                font-size: .76rem;
                letter-spacing: .16em;
                font-weight: 700;
                color: #9FD8D8;
                text-transform: uppercase;
                margin-bottom: .5rem;
            }
            .fg-title {
                font-size: clamp(1.65rem, 3.2vw, 2.6rem);
                line-height: 1.16;
                font-weight: 760;
                letter-spacing: -.02em;
                margin: 0;
            }
            .fg-subtitle {
                max-width: 760px;
                margin-top: .7rem;
                color: rgba(255,255,255,.76);
                line-height: 1.7;
                font-size: .95rem;
            }
            .fg-pill {
                display: inline-block;
                border: 1px solid rgba(255,255,255,.2);
                border-radius: 999px;
                padding: .25rem .65rem;
                font-size: .73rem;
                color: #D9E2EC;
                margin-right: .35rem;
                margin-top: .9rem;
            }
            .fg-card {
                border: 1px solid #D9E2EC;
                background: rgba(255,255,255,.88);
                border-radius: 16px;
                padding: 1rem 1.1rem;
                min-height: 108px;
                box-shadow: 0 6px 24px rgba(16,42,67,.055);
            }
            .fg-card-label {
                color: #627D98;
                font-size: .78rem;
                font-weight: 650;
                letter-spacing: .04em;
            }
            .fg-card-value {
                color: #102A43;
                font-size: 1.7rem;
                line-height: 1.4;
                font-weight: 740;
                margin-top: .2rem;
            }
            .fg-card-note {
                color: #829AB1;
                font-size: .72rem;
                margin-top: .12rem;
            }
            .fg-section-title {
                color: #102A43;
                font-size: 1.08rem;
                font-weight: 730;
                margin: .8rem 0 .55rem;
            }
            .fg-disclaimer {
                border-left: 3px solid #D9A441;
                background: #FFFAEB;
                color: #5C4300;
                padding: .8rem 1rem;
                border-radius: 0 10px 10px 0;
                font-size: .8rem;
                line-height: 1.65;
            }
            div[data-testid="stFileUploaderDropzone"] {
                border: 1.5px dashed #9FB3C8;
                border-radius: 16px;
                background: #F8FAFC;
            }
            div[data-testid="stDataFrame"] {
                border: 1px solid #D9E2EC;
                border-radius: 12px;
                overflow: hidden;
            }
            div[data-testid="stMetric"] {
                background: white;
                border: 1px solid #D9E2EC;
                border-radius: 12px;
                padding: .7rem 1rem;
            }
            .stButton > button, .stDownloadButton > button {
                border-radius: 9px;
            }
            @media (max-width: 720px) {
                .block-container { padding-top: 1rem; }
                .fg-hero { padding: 1.3rem 1.25rem; border-radius: 15px; }
                .fg-card { min-height: 92px; }
            }
            /* Dark enterprise workspace */
            .stApp {
                color: #E6F1FF;
                background:
                    radial-gradient(circle at 12% 4%, rgba(50,214,200,.12), transparent 26rem),
                    radial-gradient(circle at 92% 18%, rgba(255,107,122,.09), transparent 30rem),
                    linear-gradient(160deg, #06101C 0%, #0A1728 48%, #07111F 100%);
            }
            [data-testid="stSidebar"] {
                background: linear-gradient(180deg, #081421, #0C1B2B);
                border-right: 1px solid rgba(145,188,216,.14);
            }
            .fg-card, div[data-testid="stMetric"] {
                background: linear-gradient(145deg, rgba(18,38,59,.96), rgba(10,25,42,.96));
                border: 1px solid rgba(145,188,216,.18);
                box-shadow: 0 10px 35px rgba(0,0,0,.18);
            }
            .fg-card-label, .fg-card-note { color: #91B4CF; }
            .fg-card-value, .fg-section-title { color: #F4F9FF; }
            .fg-disclaimer {
                border-left-color: #F6D365;
                background: rgba(246,211,101,.09);
                color: #F7E8B4;
            }
            div[data-testid="stFileUploaderDropzone"] {
                border-color: rgba(50,214,200,.42);
                background: rgba(12,30,48,.82);
            }
            div[data-testid="stDataFrame"] {
                border-color: rgba(145,188,216,.2);
            }
            .fg-home-wrap {
                max-width: 1120px;
                margin: 1rem auto 0;
            }
            .fg-home-hero {
                position: relative;
                overflow: hidden;
                border-radius: 26px;
                padding: 2.7rem 2.9rem;
                border: 1px solid rgba(145,188,216,.18);
                background:
                    radial-gradient(circle at 84% 12%, rgba(50,214,200,.20), transparent 23rem),
                    linear-gradient(135deg, rgba(14,34,54,.98), rgba(7,20,34,.98));
                box-shadow: 0 30px 80px rgba(0,0,0,.28);
                margin-bottom: 1.4rem;
            }
            .fg-home-hero h1 {
                margin: .35rem 0 .7rem;
                color: #FFFFFF;
                font-size: clamp(2rem, 4vw, 3.7rem);
                letter-spacing: -.04em;
                line-height: 1.08;
            }
            .fg-home-hero p {
                max-width: 740px;
                color: #A9C0D5;
                font-size: 1rem;
                line-height: 1.8;
            }
            .fg-kicker {
                color: #32D6C8;
                font-size: .76rem;
                font-weight: 750;
                letter-spacing: .18em;
            }
            .fg-feature-card {
                min-height: 170px;
                border-radius: 20px;
                padding: 1.35rem 1.45rem;
                border: 1px solid rgba(145,188,216,.2);
                background: linear-gradient(150deg, rgba(21,44,67,.96), rgba(9,24,40,.98));
                box-shadow: 0 16px 45px rgba(0,0,0,.22);
            }
            .fg-feature-card.audit-card { border-top: 3px solid #32D6C8; }
            .fg-feature-card.ppt-card { border-top: 3px solid #F6D365; }
            .fg-feature-number {
                color: #91B4CF;
                font-size: .8rem;
                font-weight: 750;
                letter-spacing: .08em;
            }
            .fg-feature-title {
                color: #FFFFFF;
                font-size: 1.45rem;
                font-weight: 750;
                margin: .65rem 0 .45rem;
            }
            .fg-feature-desc {
                color: #9FB3C8;
                font-size: .9rem;
                line-height: 1.65;
                min-height: 3rem;
            }
            .fg-subpage-head {
                padding: .2rem 0 1rem;
                border-bottom: 1px solid rgba(145,188,216,.14);
                margin-bottom: 1rem;
            }
            .fg-subpage-head h2 {
                color: #FFFFFF;
                margin: .25rem 0 0;
                font-size: clamp(1.6rem, 3vw, 2.4rem);
            }
            .fg-data-state {
                color: #8FE3D9;
                background: rgba(50,214,200,.08);
                border: 1px solid rgba(50,214,200,.22);
                border-radius: 12px;
                padding: .65rem .9rem;
                font-size: .84rem;
            }
            .stButton > button[kind="primary"] {
                background: linear-gradient(100deg, #18A89E, #32D6C8);
                border: none;
                color: #04131A;
                font-weight: 750;
                min-height: 2.8rem;
            }
            .stButton > button[kind="secondary"] {
                border-color: rgba(145,188,216,.28);
                background: rgba(12,30,48,.78);
                color: #E6F1FF;
            }
            .stButton > button:hover {
                border-color: #32D6C8;
                color: #FFFFFF;
            }
            .stDownloadButton > button {
                border-color: rgba(50,214,200,.4);
                background: rgba(50,214,200,.12);
                color: #DFFBF7;
            }
            .stTextInput input, .stSelectbox div[data-baseweb="select"] > div {
                background: rgba(9,24,40,.9);
            }

            /* Pure light minimal theme */
            .stApp {
                color: #20242A;
                background:
                    radial-gradient(circle at 82% 8%, rgba(35,45,56,.045), transparent 22rem),
                    radial-gradient(circle at 8% 78%, rgba(35,45,56,.025), transparent 25rem),
                    #F8F9FA;
            }
            [data-testid="stHeader"] {
                background: transparent;
            }
            [data-testid="stSidebar"] {
                background: rgba(255,255,255,.96);
                border-right: 1px solid #EEF0F2;
            }
            [data-testid="stSidebar"] * {
                color: #30353B;
            }
            .block-container {
                max-width: 1180px;
                padding-top: 2rem;
            }
            .fg-minimal-home {
                position: relative;
                text-align: center;
                padding-top: clamp(1.5rem, 4vh, 3rem);
                padding-bottom: 0.8rem;
            }
            .fg-minimal-line {
                width: 54px;
                height: 1px;
                margin: 0 auto 2.3rem;
                background: linear-gradient(90deg, transparent, #7F858D 25%, #3C4147 50%, #7F858D 75%, transparent);
                opacity: .7;
            }
            .fg-minimal-home h1 {
                margin: 0;
                color: #17191C;
                font-size: clamp(2.8rem, 6vw, 5.8rem);
                line-height: 1.05;
                letter-spacing: -.055em;
                font-weight: 300;
            }
            .fg-minimal-home h1 span {
                color: #3D4248;
                font-weight: 300;
            }
            .st-key-home_actions {
                max-width: 1180px;
                margin: 1.35rem auto 0;
            }
            .st-key-home_actions [data-testid="stHorizontalBlock"] {
                gap: 1.25rem;
            }
            .st-key-open_finance button,
            .st-key-open_ppt button,
            .st-key-open_biz button,
            .st-key-open_capacity button,
            .st-key-open_simulator button,
            .st-key-open_mlrisk button,
            .st-key-open_workspace button {
                min-height: 136px;
                border-radius: 24px;
                font-size: 1.22rem;
                font-weight: 450;
                letter-spacing: .01em;
                transition: transform .18s ease, box-shadow .18s ease, background .18s ease;
            }
            .st-key-open_finance button {
                color: #FFFFFF;
                background: #17191C;
                border: 1px solid #17191C;
                box-shadow: 0 14px 32px rgba(23,25,28,.12);
            }
            .st-key-open_finance button:hover {
                color: #FFFFFF;
                background: #25282D;
                border-color: #25282D;
                transform: translateY(-2px);
                box-shadow: 0 18px 38px rgba(23,25,28,.16);
            }
            .st-key-open_ppt button {
                color: #20242A;
                background: rgba(255,255,255,.88);
                border: 1px solid #DDE1E5;
                box-shadow: 0 12px 30px rgba(35,45,56,.06);
            }
            .st-key-open_ppt button:hover {
                color: #111315;
                background: #FFFFFF;
                border-color: #BFC5CB;
                transform: translateY(-2px);
                box-shadow: 0 17px 36px rgba(35,45,56,.1);
            }
            .st-key-open_biz button {
                color: #20242A;
                background: linear-gradient(145deg, #FFFFFF, #FAF7F1);
                border: 1px solid #E5DDD0;
                box-shadow: 0 12px 30px rgba(70,58,40,.07);
            }
            .st-key-open_biz button:hover {
                color: #17191C;
                background: #FFFFFF;
                border-color: #B08C5A;
                transform: translateY(-2px);
                box-shadow: 0 17px 36px rgba(70,58,40,.11);
            }
            .st-key-open_capacity button {
                color: #20242A;
                background: linear-gradient(145deg, #FFFFFF, #F2F5F6);
                border: 1px solid #DDE2E5;
                box-shadow: 0 12px 30px rgba(35,45,56,.06);
            }
            .st-key-open_capacity button:hover {
                color: #17191C;
                background: #FFFFFF;
                border-color: #A8B0B7;
                transform: translateY(-2px);
                box-shadow: 0 17px 36px rgba(35,45,56,.11);
            }
            .st-key-open_simulator button {
                color: #20242A;
                background: linear-gradient(145deg, #FAFBFC, #EEF1F3);
                border: 1px solid #D7DDE1;
                box-shadow: 0 12px 30px rgba(35,45,56,.06);
            }
            .st-key-open_simulator button:hover {
                color: #17191C;
                background: #FFFFFF;
                border-color: #A3ABB2;
                transform: translateY(-2px);
                box-shadow: 0 17px 36px rgba(35,45,56,.11);
            }
            .st-key-open_mlrisk button {
                color: #20242A;
                background: linear-gradient(145deg, #FFFFFF, #F4F4F2);
                border: 1px solid #D8D8D4;
                box-shadow: 0 12px 30px rgba(35,45,56,.06);
            }
            .st-key-open_mlrisk button:hover {
                color: #17191C;
                background: #FFFFFF;
                border-color: #B08C5A;
                transform: translateY(-2px);
                box-shadow: 0 17px 36px rgba(70,58,40,.11);
            }
            .st-key-open_workspace button {
                color: #20242A;
                background: rgba(255,255,255,.88);
                border: 1px solid #DDE1E5;
                box-shadow: 0 12px 30px rgba(35,45,56,.06);
            }
            .st-key-open_workspace button:hover {
                color: #111315;
                background: #FFFFFF;
                border-color: #BFC5CB;
                transform: translateY(-2px);
                box-shadow: 0 17px 36px rgba(35,45,56,.1);
            }
            .fg-card, div[data-testid="stMetric"] {
                background: #FFFFFF;
                border: 1px solid #E8EBEE;
                box-shadow: 0 10px 30px rgba(35,45,56,.045);
            }
            .fg-card-label, .fg-card-note {
                color: #737B84;
            }
            .fg-card-value, .fg-section-title {
                color: #1A1D21;
            }
            .fg-subpage-head {
                border-bottom-color: #E7EAED;
            }
            .fg-subpage-head h2 {
                color: #17191C;
                font-weight: 350;
            }
            .fg-kicker {
                color: #777F88;
            }
            .fg-data-state {
                color: #384047;
                background: #FFFFFF;
                border: 1px solid #E4E7EA;
            }
            .fg-disclaimer {
                color: #5F666E;
                background: #F1F3F5;
                border-left-color: #9AA1A9;
            }
            div[data-testid="stFileUploaderDropzone"] {
                background: #FFFFFF;
                border-color: #D8DDE2;
            }
            div[data-testid="stDataFrame"] {
                border-color: #E1E5E9;
            }
            .stButton > button[kind="secondary"] {
                background: #FFFFFF;
                border-color: #D7DCE1;
                color: #24282D;
            }
            .stButton > button[kind="secondary"]:hover {
                border-color: #969EA6;
                color: #101214;
            }
            .stDownloadButton > button {
                background: #FFFFFF;
                color: #20242A;
                border-color: #D5DAE0;
            }
            .stTextInput input, .stSelectbox div[data-baseweb="select"] > div {
                background: #FFFFFF;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

    def hero(self) -> None:
        st.markdown(
            """
            <div class="fg-hero">
              <div class="fg-eyebrow">FINGUARD · FINANCIAL INTEGRITY INTELLIGENCE</div>
              <h1 class="fg-title">企业财务异常合规审计系统</h1>
              <div class="fg-subtitle">
                用可解释证据链识别财务蛀虫与基层微腐败：规则硬约束、统计异常检验、
                交叉行为验证，再由大模型生成可复核的审计指引。
              </div>
              <div>
                <span class="fg-pill">本地清洗</span>
                <span class="fg-pill">全链路可解释</span>
                <span class="fg-pill">AI 辅助而非替代</span>
                <span class="fg-pill">Excel 底稿导出</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    def sidebar(self, default_model: str = "deepseek-chat") -> dict[str, Any]:
        with st.sidebar:
            st.markdown("### FinGuard 控制台")
            st.caption("Enterprise audit workspace")
            st.divider()
            st.markdown("**AI 研判**")
            auto_ai = st.toggle(
                "自动研判全部高风险凭证",
                value=True,
                help="Risk_Score > 60 的凭证将逐笔调用 DeepSeek API。",
            )
            model = st.text_input(
                "模型 ID",
                value=default_model,
                help="官方模型 ID 为 deepseek-chat 或 deepseek-reasoner。",
            )
            st.caption("API 密钥优先使用当前会话临时 Key，否则读取 Secrets。")
            st.divider()
            st.markdown("**风险矩阵口径**")
            st.caption("硬规则 +30 · 统计异常 +40 · 交叉验证 +30 · 封顶 100")
            st.caption("中风险 30–60；高风险严格大于 60。")
            st.divider()
            st.caption("社会价值：让中小组织和公共机构也能获得可负担的审计线索筛查能力。")
            st.caption("开源倡议：部署者应共同完善规则、反偏见测试与数据治理。")
        return {
            "auto_ai": auto_ai,
            "model": model.strip(),
        }

    def render_metrics(self, risk_metrics: dict[str, Any]) -> None:
        items = [
            (
                "凭证总笔数",
                f"{int(risk_metrics.get('total_count', 0)):,}",
                "清洗后有效会计记录",
            ),
            (
                "凭证总金额",
                format_cny(risk_metrics.get("total_amount", 0)),
                "按单笔借贷方较大绝对值汇总",
            ),
            (
                "拦截高风险笔数",
                f"{int(risk_metrics.get('high_risk_count', 0)):,}",
                "Risk_Score 严格大于 60",
            ),
            (
                "高风险金额",
                format_cny(risk_metrics.get("high_risk_amount", 0)),
                "需进入人工核查队列",
            ),
        ]
        columns = st.columns(4)
        for column, (label, value, note) in zip(columns, items, strict=True):
            with column:
                st.markdown(
                    f"""
                    <div class="fg-card">
                      <div class="fg-card-label">{html.escape(label)}</div>
                      <div class="fg-card-value">{html.escape(value)}</div>
                      <div class="fg-card-note">{html.escape(note)}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    def render_charts(self, data: pd.DataFrame) -> None:
        anomalies = data.loc[data["Risk_Score"] >= 30].copy()
        left, right = st.columns([1, 1.35])
        with left:
            st.markdown(
                '<div class="fg-section-title">异常金额：制单人占比</div>', unsafe_allow_html=True
            )
            if anomalies.empty:
                st.info("当前没有中高风险异常。")
            else:
                by_user = (
                    anomalies.groupby("user", as_index=False)
                    .agg(amount=("amount", "sum"), count=("voucher_id", "count"))
                    .sort_values("amount", ascending=False)
                )
                figure = px.pie(
                    by_user,
                    names="user",
                    values="amount",
                    hole=0.58,
                    color_discrete_sequence=[
                        "#8B1E2D",
                        "#C05A63",
                        "#D9A441",
                        "#167D7F",
                        "#1F5A7A",
                        "#829AB1",
                    ],
                    hover_data={"count": True, "amount": ":,.2f"},
                )
                figure.update_traces(
                    textposition="outside",
                    textinfo="percent",
                    hovertemplate="%{label}<br>异常金额 ¥%{value:,.2f}<br>%{customdata[0]} 笔<extra></extra>",
                )
                figure.add_annotation(
                    text=f"<b>{len(anomalies)}</b><br>异常笔数",
                    x=0.5,
                    y=0.5,
                    showarrow=False,
                    font={"size": 16, "color": PRIMARY},
                )
                figure.update_layout(
                    height=345,
                    margin={"l": 12, "r": 12, "t": 16, "b": 10},
                    legend={"orientation": "h", "y": -0.08},
                    paper_bgcolor="rgba(0,0,0,0)",
                    font={"family": "Microsoft YaHei, sans-serif", "color": PRIMARY},
                )
                st.plotly_chart(figure, width="stretch", theme=None)

        with right:
            st.markdown(
                '<div class="fg-section-title">异常发生日期趋势</div>', unsafe_allow_html=True
            )
            if anomalies.empty:
                st.info("当前没有中高风险异常。")
            else:
                trend = anomalies.copy()
                trend["发生日期"] = pd.to_datetime(trend["date"]).dt.normalize()
                daily = (
                    trend.groupby("发生日期", as_index=False)
                    .agg(异常笔数=("voucher_id", "count"), 异常金额=("amount", "sum"))
                    .sort_values("发生日期")
                )
                figure = px.line(
                    daily,
                    x="发生日期",
                    y="异常笔数",
                    markers=True,
                    hover_data={"异常金额": ":,.2f"},
                    color_discrete_sequence=[TEAL],
                )
                figure.update_traces(
                    line={"width": 3},
                    marker={"size": 8, "line": {"width": 2, "color": "#FFFFFF"}},
                    hovertemplate="%{x|%Y-%m-%d}<br>异常 %{y} 笔<br>异常金额 ¥%{customdata[0]:,.2f}<extra></extra>",
                )
                figure.update_layout(
                    height=345,
                    margin={"l": 12, "r": 12, "t": 16, "b": 10},
                    xaxis={"title": None, "gridcolor": "#E9EFF4"},
                    yaxis={"title": "异常笔数", "gridcolor": "#E9EFF4", "rangemode": "tozero"},
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font={"family": "Microsoft YaHei, sans-serif", "color": PRIMARY},
                )
                st.plotly_chart(figure, width="stretch", theme=None)

    def render_table(self, data: pd.DataFrame) -> None:
        st.markdown('<div class="fg-section-title">风险凭证明细</div>', unsafe_allow_html=True)
        filter_col_1, filter_col_2, filter_col_3 = st.columns([1, 1, 2])
        with filter_col_1:
            level = st.selectbox(
                "风险等级",
                ["全部", "高风险", "中风险", "低风险"],
                label_visibility="collapsed",
            )
        with filter_col_2:
            minimum_score = st.slider(
                "最低风险分",
                min_value=0,
                max_value=100,
                value=0,
                step=10,
                label_visibility="collapsed",
            )
        with filter_col_3:
            query = st.text_input(
                "筛选凭证号、制单人或摘要",
                placeholder="筛选凭证号、制单人或摘要…",
                label_visibility="collapsed",
            )

        filtered = data.loc[data["Risk_Score"] >= minimum_score].copy()
        if level != "全部":
            filtered = filtered.loc[filtered["Risk_Level"] == level]
        if query.strip():
            needle = query.strip()
            mask = (
                filtered["voucher_id"]
                .astype(str)
                .str.contains(needle, case=False, na=False, regex=False)
                | filtered["user"]
                .astype(str)
                .str.contains(needle, case=False, na=False, regex=False)
                | filtered["summary"]
                .astype(str)
                .str.contains(needle, case=False, na=False, regex=False)
            )
            filtered = filtered.loc[mask]

        display_columns = [
            "voucher_id",
            "date",
            "user",
            "summary",
            "transaction_type",
            "amount",
            "Risk_Score",
            "Risk_Level",
            "Risk_Features",
            "ai_advice",
            "ai_status",
            "is_human_verified_match",
        ]
        display = filtered[display_columns].rename(
            columns={
                "voucher_id": "凭证号",
                "date": "日期",
                "user": "制单人",
                "summary": "摘要",
                "transaction_type": "业务类型",
                "amount": "金额",
                "Risk_Score": "风险分",
                "Risk_Level": "等级",
                "Risk_Features": "触发特征",
                "ai_advice": "AI 审计研判",
                "ai_status": "AI 状态",
                "is_human_verified_match": "人工反馈",
            }
        )

        def highlight_row(row: pd.Series) -> list[str]:
            score = float(row["风险分"])
            if score > 60:
                return ["background-color: #6E1521; color: #FFFFFF; border-color: #4A0D16"] * len(
                    row
                )
            if score >= 30:
                return ["background-color: #FFF2CC; color: #4C3A00; border-color: #E6C85C"] * len(
                    row
                )
            return [""] * len(row)

        styled = (
            display.style.apply(highlight_row, axis=1)
            .format({"金额": "¥{:,.2f}", "风险分": "{:.0f}"})
            .set_properties(**{"font-size": "12px"})
        )
        st.dataframe(
            styled,
            width="stretch",
            height=620,
            hide_index=True,
            column_config={
                "日期": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
                "摘要": st.column_config.TextColumn(width="large"),
                "触发特征": st.column_config.TextColumn(width="large"),
                "AI 审计研判": st.column_config.TextColumn(width="large"),
            },
        )
        st.caption(f"当前展示 {len(display):,} 笔。深红行：Risk_Score > 60；黄色行：30–60。")

    def render_quality(
        self,
        ingestion_report: dict[str, Any],
        rejected_data: pd.DataFrame,
        warnings: list[str],
    ) -> None:
        with st.expander("数据清洗与质量证据", expanded=False):
            quality_columns = st.columns(4)
            quality_items = [
                ("原始行数", ingestion_report.get("raw_rows", "—")),
                ("有效凭证", ingestion_report.get("clean_rows", "—")),
                ("隔离记录", len(rejected_data)),
                ("质量结论", ingestion_report.get("quality", "—")),
            ]
            for column, (label, value) in zip(
                quality_columns,
                quality_items,
                strict=True,
            ):
                with column:
                    st.metric(label, str(value))
            st.caption(f"文件指纹（SHA-256）：`{ingestion_report.get('source_sha256', '—')}`")
            for warning in warnings:
                st.warning(warning, icon="⚠️")
            if not rejected_data.empty:
                st.markdown("**隔离行（不会进入风险评分）**")
                preview = rejected_data.copy()
                visible = [
                    column
                    for column in (
                        "_source_row",
                        "voucher_id",
                        "date",
                        "user",
                        "summary",
                        "debit",
                        "credit",
                        "_reject_reason",
                    )
                    if column in preview.columns
                ]
                st.dataframe(
                    preview[visible],
                    width="stretch",
                    hide_index=True,
                )
                st.download_button(
                    "下载隔离记录 CSV",
                    data=rejected_data.to_csv(index=False).encode("utf-8-sig"),
                    file_name="FinGuard_隔离记录.csv",
                    mime="text/csv",
                )
            with st.popover("系统如何计分？"):
                st.markdown(
                    """
                    - **硬规则 +30**：金额大于样本均值 3 倍；同日同人金额差异小于 1%；
                      周末、中国法定节假日或 00:00–05:00；模糊摘要且金额巨大、缺少明细。
                    - **统计异常 +40**：稳健 IQR 离群（默认 1.5×IQR）或 Z-score 绝对值 > 3；
                      默认优先使用对极端值更稳健的 IQR。
                    - **交叉验证 +30**：同一制单人在周末凌晨提交至少两笔接近整数的大额凭证。
                    - 同一层只计分一次，但所有触发特征逐项保留；总分封顶 100。
                    """
                )

    def footer(self) -> None:
        st.markdown("---")
        st.markdown(
            '<div class="fg-disclaimer">'
            "合规提示：FinGuard 输出的是审计线索与待核查假设，不构成舞弊或违法事实认定。"
            "处理真实数据前，请完成授权、最小化字段、访问控制、日志留存和供应商数据协议审查。"
            "启用 AI 表示相应凭证的必要字段将发送至所配置的模型服务。"
            "</div>",
            unsafe_allow_html=True,
        )
        st.caption(version_label())
