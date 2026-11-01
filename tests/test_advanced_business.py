from __future__ import annotations

import pytest

from finguard.advanced_business import (
    CLEARING_ACCOUNT,
    GAIN_LOSS_ACCOUNT,
    build_asset_disposal_entries,
    is_asset_disposal_business,
)
from finguard.manufacturing_engine import convert_business
from finguard.v04_hitl import recalculate_reports


def _totals(entries: list[dict]) -> tuple[float, float]:
    debit = sum(float(entry["amount"]) for entry in entries if entry["debit"])
    credit = sum(float(entry["amount"]) for entry in entries if entry["credit"])
    return round(debit, 2), round(credit, 2)


def test_office_equipment_disposal_without_gain_or_loss() -> None:
    business = "转让一台闲置办公设备，收款20000元，该设备原值50000，已提折旧30000。"
    result = build_asset_disposal_entries(business)

    assert is_asset_disposal_business(business)
    assert result.original_value == 50000.0
    assert result.accumulated_depreciation == 30000.0
    assert result.net_book_value == 20000.0
    assert result.proceeds == 20000.0
    assert result.gain_loss == 0.0
    assert len(result.entries) == 5
    assert _totals(result.entries) == (70000.0, 70000.0)


def test_old_vehicle_disposal_creates_asset_disposal_gain() -> None:
    business = "卖旧车，收到转账45000元，原值80000，折旧50000。"
    result = build_asset_disposal_entries(business)

    assert result.net_book_value == 30000.0
    assert result.proceeds == 45000.0
    assert result.gain_loss == 15000.0
    assert len(result.entries) == 6
    assert _totals(result.entries) == (140000.0, 140000.0)
    assert any(
        entry["debit"] == CLEARING_ACCOUNT
        and entry["credit"] == GAIN_LOSS_ACCOUNT
        and entry["amount"] == 15000.0
        for entry in result.entries
    )
    assert all("主营业务收入" not in entry.values() for entry in result.entries)
    assert all("应交税费-应交增值税" not in str(entry.values()) for entry in result.entries)


def test_asset_disposal_router_bypasses_ai_and_keeps_reports_isolated() -> None:
    calls: list[int] = []

    class FailingClient:
        chat = None

    business = "卖旧车，收到转账45000元，原值80000，折旧50000。"
    result = convert_business(
        business,
        api_key="test-key",
        client=FailingClient(),
    )

    assert calls == []
    assert len(result.records) == 6
    assert all(record["calculation_source"] == "资产处置专用规则" for record in result.records)
    assert all(
        record["credit"] != "主营业务收入"
        and "应交税费-应交增值税" not in record["debit"]
        and "应交税费-应交增值税" not in record["credit"]
        for record in result.records
    )
    reports = recalculate_reports(result.records)
    income = reports["income_statement"]
    balance = reports["balance_sheet"]
    assert income.loc[income["项目"] == "一、营业收入", "金额"].iloc[0] == 0.0
    assert income.loc[income["项目"] == "加：资产处置损益", "金额"].iloc[0] == 15000.0
    assert income.loc[income["项目"] == "三、净利润", "金额"].iloc[0] == 15000.0
    assert balance.loc[balance["项目"] == "平衡校验", "金额"].iloc[0] == 0.0


@pytest.mark.parametrize(
    ("business", "expected_gain"),
    (
        (
            "转让一台闲置办公设备，收款20000元，该设备原值50000，已提折旧30000。",
            0.0,
        ),
        (
            "卖旧车，收到转账45000元，原值80000，折旧50000。",
            15000.0,
        ),
    ),
)
def test_requested_asset_disposals_are_balanced(
    business: str,
    expected_gain: float,
) -> None:
    result = convert_business(business, api_key="")

    debit_total, credit_total = _totals(result.records)
    assert debit_total == credit_total
    reports = recalculate_reports(result.records)
    balance = reports["balance_sheet"]
    income = reports["income_statement"]
    assert balance.loc[balance["项目"] == "平衡校验", "金额"].iloc[0] == 0.0
    assert income.loc[income["项目"] == "加：资产处置损益", "金额"].iloc[0] == expected_gain
