from __future__ import annotations

import io

import pandas as pd
import pytest

from finguard.capacity_engine import (
    CapacityAsset,
    CapacityInputError,
    default_assets,
    load_asset_ledger,
    parse_asset_dataframe,
    simulate_dynamic_capacity,
)


def test_default_mock_uses_first_tied_line_as_bottleneck() -> None:
    assets = default_assets()
    result = simulate_dynamic_capacity(assets, 300, 5)

    assert len(assets) == 2
    assert result.bottleneck_line == "割草机产线 A"
    assert result.bottleneck_capacity == 60.0
    assert result.total_limit_capacity == 300.0
    assert result.sufficient


def test_bottleneck_controls_delivery_days_not_total_capacity_sum() -> None:
    assets = [
        CapacityAsset("开料机", 2, 500.0),
        CapacityAsset("封边机", 1, 600.0),
        CapacityAsset("喷漆线", 1, 200.0),
        CapacityAsset("组装台", 2, 150.0),
    ]

    result = simulate_dynamic_capacity(assets, 100000, 100)

    assert result.bottleneck_line == "喷漆线"
    assert result.bottleneck_capacity == 200.0
    assert result.total_limit_capacity == 20000.0
    assert not result.sufficient
    assert result.required_completion_days == 500
    assert result.capacity_shortfall == 80000.0


def test_home_factory_csv_detects_paint_line_bottleneck() -> None:
    payload = (
        "产线名称,产线数量,单台日产能\n开料机,2,500\n封边机,1,600\n喷漆线,1,200\n组装台,2,150\n"
    ).encode("utf-8-sig")
    assets = load_asset_ledger(payload, "家居厂固定资产.csv")

    result = simulate_dynamic_capacity(assets, 100000, 30)

    assert result.bottleneck_line == "喷漆线"
    assert result.required_completion_days == 500
    assert not result.sufficient


def test_invalid_rows_are_filtered_instead_of_crashing() -> None:
    dataframe = pd.DataFrame(
        {
            "资产名称": ["有效线", "零数量", "零产能", "乱码产能"],
            "数量": ["2台", 0, 1, "三台"],
            "单台日产能": ["80台/天", 100, 0, "未知"],
        }
    )

    assets = parse_asset_dataframe(dataframe)

    assert [(asset.line_name, asset.daily_capacity) for asset in assets] == [("有效线", 160.0)]


def test_quantity_alias_and_decimal_text_are_parsed() -> None:
    dataframe = pd.DataFrame(
        {
            "生产线名称": ["A 线"],
            "产线数量": ["2条"],
            "单线日产": ["120.5件/天"],
        }
    )

    assets = parse_asset_dataframe(dataframe)

    assert assets[0].daily_capacity == 241.0


def test_xlsx_asset_ledger_is_parsed() -> None:
    output = io.BytesIO()
    pd.DataFrame(
        {
            "产线名称": ["A 线"],
            "数量": [3],
            "单台日产能": [50],
        }
    ).to_excel(output, index=False)

    assets = load_asset_ledger(output.getvalue(), "assets.xlsx")

    assert assets[0].daily_capacity == 150.0


def test_asset_ledger_missing_column_is_rejected() -> None:
    dataframe = pd.DataFrame({"资产名称": ["A"], "数量": [1]})

    with pytest.raises(CapacityInputError, match="单台日产能"):
        parse_asset_dataframe(dataframe)


@pytest.mark.parametrize(
    ("order_quantity", "days", "message"),
    (
        (0, 5, "订单数量必须大于 0"),
        (100, 0, "要求天数必须大于 0"),
        (100, 2.5, "要求天数必须是整数"),
    ),
)
def test_dynamic_capacity_rejects_invalid_order_input(
    order_quantity: float,
    days: float,
    message: str,
) -> None:
    with pytest.raises(CapacityInputError, match=message):
        simulate_dynamic_capacity(default_assets(), order_quantity, days)
