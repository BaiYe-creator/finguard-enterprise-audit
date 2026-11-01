"""Capacity sandbox page with isolated V0.4 state and bottleneck scheduling."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import streamlit as st

from finguard.capacity_engine import (
    CapacityInputError,
    default_assets,
    load_asset_ledger,
    simulate_dynamic_capacity,
)
from finguard.capacity_engine import excel_sheet_names as capacity_excel_sheet_names

PageHeader = Callable[[str, str, str], None]


def render_capacity_page(render_header: PageHeader) -> None:
    render_header(
        "产能推演沙盘",
        "CAPACITY SANDBOX",
        "back_from_capacity",
    )
    st.caption(
        "上传固定资产台账后，系统直接按产线数量和单台日产能计算。"
        "沙盘状态完全隔离，不会写入 V0.3 财务账本或审计报表。"
    )

    uploaded_assets = st.file_uploader(
        "上传固定资产台账",
        type=["csv", "xlsx"],
        key="v04_capacity_asset_uploader",
        help="需要包含：产线名称（或资产名称）、数量、单台日产能。",
    )
    asset_error = ""
    asset_notice = ""

    if uploaded_assets is not None:
        payload = uploaded_assets.getvalue()
        selected_sheet: str | None = None
        if Path(uploaded_assets.name).suffix.casefold() == ".xlsx":
            try:
                sheets = capacity_excel_sheet_names(payload)
            except Exception as exc:
                asset_error = f"Excel 工作表读取失败：{exc}"
                sheets = []
            if len(sheets) > 1:
                selected_sheet = st.selectbox(
                    "选择资产台账工作表",
                    sheets,
                    key="v04_capacity_asset_sheet",
                )
            elif sheets:
                selected_sheet = sheets[0]

        signature = hashlib.sha256(
            f"{uploaded_assets.name}|{selected_sheet or ''}|".encode() + payload
        ).hexdigest()
        if not asset_error and signature != st.session_state.get("v04_capacity_asset_signature"):
            try:
                parsed_assets = load_asset_ledger(
                    payload,
                    uploaded_assets.name,
                    sheet_name=selected_sheet,
                )
            except Exception as exc:
                asset_error = f"固定资产台账解析失败：{exc}"
                st.session_state.v04_capacity_assets = [
                    asset.to_dict() for asset in default_assets()
                ]
                st.session_state.v04_capacity_asset_source = "Mock 默认台账"
                st.session_state.v04_capacity_asset_signature = signature
                st.session_state.v04_capacity_result = None
            else:
                st.session_state.v04_capacity_assets = [asset.to_dict() for asset in parsed_assets]
                st.session_state.v04_capacity_asset_source = f"{uploaded_assets.name}" + (
                    f" / {selected_sheet}" if selected_sheet else ""
                )
                st.session_state.v04_capacity_asset_signature = signature
                st.session_state.v04_capacity_result = None
                asset_notice = (
                    f"已识别 {len(parsed_assets)} 条产线资产记录，可继续输入订单数量并计算。"
                )
    else:
        if st.session_state.get("v04_capacity_asset_source") != "Mock 默认台账":
            st.session_state.v04_capacity_assets = [asset.to_dict() for asset in default_assets()]
            st.session_state.v04_capacity_asset_source = "Mock 默认台账"
            st.session_state.v04_capacity_asset_signature = None
            st.session_state.v04_capacity_result = None
        st.session_state.v04_capacity_asset_notice = (
            "未上传资产台账，当前使用 2 条 Mock 产线，每条单线日产 60 台。"
        )

    if asset_error:
        st.error(asset_error)
        st.session_state.v04_capacity_asset_notice = "资产文件无法使用，已切换到 Mock 默认台账。"
    elif asset_notice:
        st.session_state.v04_capacity_asset_notice = asset_notice

    asset_rows = st.session_state.get("v04_capacity_assets") or [
        asset.to_dict() for asset in default_assets()
    ]
    if not st.session_state.get("v04_capacity_assets"):
        st.session_state.v04_capacity_assets = asset_rows
        st.session_state.v04_capacity_asset_source = "Mock 默认台账"

    if st.session_state.get("v04_capacity_asset_notice"):
        st.caption(st.session_state.v04_capacity_asset_notice)
    st.caption(f"资产来源：{st.session_state.get('v04_capacity_asset_source', 'Mock 默认台账')}")
    st.dataframe(
        pd.DataFrame(asset_rows),
        width="stretch",
        hide_index=True,
    )
    daily_capacity_total = sum(float(row["日总产能"]) for row in asset_rows)
    st.caption(
        f"共 {len({row['产线名称'] for row in asset_rows}):,} 条产线，"
        f"{sum(int(row['数量']) for row in asset_rows):,} 台设备，"
        f"日总产能 {daily_capacity_total:,.2f} 台。"
    )
    bottleneck_asset = min(asset_rows, key=lambda row: float(row["日总产能"]))
    st.warning(
        f"产能瓶颈提示：当前工厂受限于【{bottleneck_asset['产线名称']}】，"
        f"日极限产能为 {float(bottleneck_asset['日总产能']):,.2f} 件。"
    )

    result = st.session_state.get("v04_capacity_result")
    if result and (
        "asset_lines" not in result
        or "bottleneck_capacity" not in result
        or "required_completion_days" not in result
    ):
        st.session_state.v04_capacity_result = None
        result = None

    with st.form("v04_capacity_form"):
        order_column, days_column = st.columns(2)
        with order_column:
            order_quantity = st.number_input(
                "订单数量（台）",
                min_value=1,
                value=500,
                step=1,
                key="v04_capacity_order_quantity",
            )
        with days_column:
            required_days = st.number_input(
                "要求完成天数",
                min_value=1,
                value=5,
                step=1,
                key="v04_capacity_required_days",
            )
        submitted = st.form_submit_button(
            "计算产能",
            type="primary",
            width="stretch",
        )

    if submitted:
        try:
            result = simulate_dynamic_capacity(
                asset_rows,
                order_quantity,
                required_days,
            )
        except CapacityInputError as exc:
            st.error(str(exc))
        else:
            st.session_state.v04_capacity_result = result.to_dict()

    result = st.session_state.get("v04_capacity_result")
    if not result:
        st.markdown(
            '<div class="fg-data-state">输入订单数量和交付天数后开始推演。</div>',
            unsafe_allow_html=True,
        )
        return

    if result["sufficient"]:
        st.success("产能充足，按计划排产即可。")
    else:
        st.error(
            "严重超出产能极限！基于当前瓶颈，此订单需至少 "
            f"{int(result['required_completion_days']):,} 天才能完成。"
            "建议：拒绝接单、延期交货或外包，"
            "单纯购买设备无法解决瓶颈卡脖子问题！"
        )

    metric_left, metric_right = st.columns(2)
    with metric_left:
        st.metric(
            "瓶颈日产能",
            f"{float(result['bottleneck_capacity']):,.2f} 件",
        )
        st.metric(
            "交付期极限产能",
            f"{float(result['total_limit_capacity']):,.2f} 件",
        )
    with metric_right:
        st.metric(
            "实际最少天数",
            f"{int(result['required_completion_days']):,} 天",
        )
        st.metric(
            "订单缺口",
            f"{float(result['capacity_shortfall']):,.2f} 件",
        )

    st.caption(
        f"木桶口径：瓶颈【{result['bottleneck_line']}】"
        f"{float(result['bottleneck_capacity']):,.2f} 件/天 × "
        f"{int(result['required_days'])} 天。"
    )
