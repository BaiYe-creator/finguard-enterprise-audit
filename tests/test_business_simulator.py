from __future__ import annotations

import pytest

from finguard.business_simulator import (
    SimulationInputError,
    format_money,
    simulate_business,
)


def test_zero_quantity_or_price_is_rejected_before_division() -> None:
    with pytest.raises(SimulationInputError, match="数量与单价不可为 0"):
        simulate_business(0, 100, 60)
    with pytest.raises(SimulationInputError, match="数量与单价不可为 0"):
        simulate_business(100, 0, 60)


def test_core_calculation_is_cached() -> None:
    assert callable(simulate_business.clear)


def test_loss_and_high_margin_are_calculated_precisely() -> None:
    loss = simulate_business(100, 100, 120)
    assert loss.unit_profit == -20.0
    assert loss.total_profit == -2000.0
    assert loss.gross_margin == -0.2

    strong = simulate_business(100, 100, 10)
    assert strong.gross_margin == pytest.approx(0.9)
    assert strong.total_profit == 9000.0


def test_capacity_linkage_is_skipped_for_zero_or_none() -> None:
    none_capacity = simulate_business(100, 100, 60, None)
    zero_capacity = simulate_business(100, 100, 60, 0)

    assert none_capacity.delivery_days is None
    assert zero_capacity.delivery_days is None
    assert not none_capacity.exceeds_30_days
    assert not zero_capacity.exceeds_30_days


def test_capacity_linkage_flags_long_delivery() -> None:
    result = simulate_business(1000, 100, 60, 20)

    assert result.delivery_days == 50.0
    assert result.exceeds_30_days


@pytest.mark.parametrize(
    ("amount", "expected"),
    (
        (12_345_678_900, "123.46 亿元"),
        (12_345_678, "1234.57 万元"),
        (9_999.5, "9999.50 元"),
        (-15_000, "-1.50 万元"),
    ),
)
def test_money_formatting(amount: float, expected: str) -> None:
    assert format_money(amount) == expected
