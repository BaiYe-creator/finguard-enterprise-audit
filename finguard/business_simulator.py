"""Cached What-If business simulation calculations for FinGuard V0.5."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import streamlit as st


class SimulationInputError(ValueError):
    """Raised when simulator inputs cannot be evaluated safely."""


@dataclass(frozen=True)
class SimulationResult:
    order_quantity: int
    unit_price: float
    unit_cost: float
    revenue: float
    total_cost: float
    unit_profit: float
    gross_margin: float
    total_profit: float
    delivery_days: float | None
    exceeds_30_days: bool

    def to_dict(self) -> dict[str, float | int | bool | None]:
        return asdict(self)


def format_money(value: float) -> str:
    amount = float(value)
    if abs(amount) >= 100_000_000:
        return f"{amount / 100_000_000:.2f} 亿元"
    if abs(amount) >= 10_000:
        return f"{amount / 10_000:.2f} 万元"
    return f"{amount:.2f} 元"


@st.cache_data(show_spinner=False)
def simulate_business(
    order_quantity: int,
    unit_price: float,
    unit_cost: float,
    bottleneck_capacity: float | None = None,
) -> SimulationResult:
    """Run deterministic business math with explicit divide-by-zero guards."""

    if order_quantity <= 0 or unit_price <= 0:
        raise SimulationInputError("输入参数不合法，数量与单价不可为 0")
    if unit_cost < 0:
        raise SimulationInputError("单位成本不能为负数")

    quantity = int(order_quantity)
    price = float(unit_price)
    cost = float(unit_cost)
    revenue = round(quantity * price, 2)
    total_cost = round(quantity * cost, 2)
    unit_profit = round(price - cost, 2)
    gross_margin = round(unit_profit / price, 6) if price > 0 else 0.0
    total_profit = round(revenue - total_cost, 2)

    delivery_days: float | None = None
    exceeds_30_days = False
    if bottleneck_capacity is not None and math.isfinite(float(bottleneck_capacity)):
        capacity = float(bottleneck_capacity)
        if capacity > 0:
            delivery_days = round(quantity / capacity, 2)
            exceeds_30_days = delivery_days > 30

    return SimulationResult(
        order_quantity=quantity,
        unit_price=price,
        unit_cost=cost,
        revenue=revenue,
        total_cost=total_cost,
        unit_profit=unit_profit,
        gross_margin=gross_margin,
        total_profit=total_profit,
        delivery_days=delivery_days,
        exceeds_30_days=exceeds_30_days,
    )
