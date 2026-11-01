"""What-if business simulator page with isolated V0.5 state."""

from __future__ import annotations

import math
from collections.abc import Callable

import streamlit as st

from finguard.business_simulator import (
    SimulationInputError,
    format_money,
    simulate_business,
)

PageHeader = Callable[[str, str, str], None]


def _ensure_v05_pair(canonical_key: str, input_key: str, default: float) -> None:
    if canonical_key not in st.session_state:
        st.session_state[canonical_key] = default
    if input_key not in st.session_state:
        st.session_state[input_key] = st.session_state[canonical_key]


def _sync_v05_from_slider(canonical_key: str, input_key: str) -> None:
    st.session_state[input_key] = st.session_state[canonical_key]


def _sync_v05_from_input(canonical_key: str, input_key: str) -> None:
    st.session_state[canonical_key] = st.session_state[input_key]


def render_simulator_page(render_header: PageHeader) -> None:
    render_header(
        "经营模拟器",
        "WHAT-IF SIMULATION",
        "back_from_simulator",
    )
    st.caption("调整订单数量、单价与单位成本，实时观察利润、毛利率与交付周期。")

    _ensure_v05_pair("v05_order_qty", "v05_order_qty_input", 1000)
    _ensure_v05_pair("v05_unit_price", "v05_unit_price_input", 100.0)
    _ensure_v05_pair("v05_unit_cost", "v05_unit_cost_input", 70.0)

    quantity_slider, quantity_input = st.columns(
        [2.6, 1],
        vertical_alignment="bottom",
    )
    with quantity_slider:
        st.slider(
            "接单数量（拖动估算）",
            min_value=0,
            max_value=1_000_000,
            step=1,
            key="v05_order_qty",
            on_change=_sync_v05_from_slider,
            args=("v05_order_qty", "v05_order_qty_input"),
        )
    with quantity_input:
        st.number_input(
            "精确数量",
            min_value=0,
            max_value=1_000_000,
            step=1,
            key="v05_order_qty_input",
            on_change=_sync_v05_from_input,
            args=("v05_order_qty", "v05_order_qty_input"),
        )

    price_slider, price_input = st.columns(
        [2.6, 1],
        vertical_alignment="bottom",
    )
    with price_slider:
        st.slider(
            "单价（拖动估算）",
            min_value=0.0,
            max_value=100_000.0,
            step=0.01,
            key="v05_unit_price",
            on_change=_sync_v05_from_slider,
            args=("v05_unit_price", "v05_unit_price_input"),
        )
    with price_input:
        st.number_input(
            "精确单价",
            min_value=0.0,
            max_value=100_000.0,
            step=0.01,
            format="%.2f",
            key="v05_unit_price_input",
            on_change=_sync_v05_from_input,
            args=("v05_unit_price", "v05_unit_price_input"),
        )

    cost_slider, cost_input = st.columns(
        [2.6, 1],
        vertical_alignment="bottom",
    )
    with cost_slider:
        st.slider(
            "单位成本（拖动估算）",
            min_value=0.0,
            max_value=100_000.0,
            step=0.01,
            key="v05_unit_cost",
            on_change=_sync_v05_from_slider,
            args=("v05_unit_cost", "v05_unit_cost_input"),
        )
    with cost_input:
        st.number_input(
            "精确单位成本",
            min_value=0.0,
            max_value=100_000.0,
            step=0.01,
            format="%.2f",
            key="v05_unit_cost_input",
            on_change=_sync_v05_from_input,
            args=("v05_unit_cost", "v05_unit_cost_input"),
        )

    order_quantity = int(st.session_state["v05_order_qty"])
    unit_price = float(st.session_state["v05_unit_price"])
    unit_cost = float(st.session_state["v05_unit_cost"])

    if order_quantity <= 0 or unit_price <= 0:
        st.error("输入参数不合法，数量与单价不可为 0")
        st.stop()

    capacity_result = st.session_state.get("v04_capacity_result") or {}
    raw_bottleneck = capacity_result.get("bottleneck_capacity")
    bottleneck_capacity = None
    if raw_bottleneck is not None:
        try:
            parsed_bottleneck = float(raw_bottleneck)
        except (TypeError, ValueError):
            parsed_bottleneck = 0.0
        if math.isfinite(parsed_bottleneck) and parsed_bottleneck > 0:
            bottleneck_capacity = parsed_bottleneck

    try:
        result = simulate_business(
            order_quantity,
            unit_price,
            unit_cost,
            bottleneck_capacity,
        )
    except SimulationInputError as exc:
        st.error(str(exc))
        st.stop()

    st.session_state.v05_last_sim_result = result.to_dict()
    metric_left, metric_right = st.columns(2)
    with metric_left:
        st.metric("预计收入", format_money(result.revenue))
        st.metric("预计总成本", format_money(result.total_cost))
    with metric_right:
        st.metric("预计总利润", format_money(result.total_profit))
        st.metric("毛利率", f"{result.gross_margin * 100:.2f}%")

    margin_percent = result.gross_margin * 100
    if result.unit_profit < 0:
        st.error(f"推演否决：导致单件亏损 {abs(result.unit_profit):.2f} 元，此单不可接。")
    elif result.gross_margin < 0.15:
        st.warning(f"利润预警：毛利率仅 {margin_percent:.2f}%，抗风险能力极低。")
    else:
        st.success(f"推演通过：预计总利润 {format_money(result.total_profit)}。")

    if result.delivery_days is not None:
        st.caption(
            f"按当前瓶颈日产能 {bottleneck_capacity:,.2f} 件测算，"
            f"订单预计需要 {result.delivery_days:,.2f} 天。"
        )
    if result.exceeds_30_days:
        st.info("注意：交付周期超 30 天，资金占用极大。")
