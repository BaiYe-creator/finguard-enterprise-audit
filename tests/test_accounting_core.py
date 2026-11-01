from __future__ import annotations

import io

import pytest
from openpyxl import load_workbook

from finguard.accounting_core import (
    balance_sheet_rows,
    balance_sheet_values,
    classify_account,
    income_statement_rows,
    income_statement_values,
    statement_figures,
    trial_balance,
)
from finguard.advanced_business import build_asset_disposal_entries
from finguard.manufacturing_engine import (
    build_balance_sheet_xlsx,
    build_income_statement_xlsx,
)
from finguard.v04_hitl import balance_sheet as v04_balance_sheet
from finguard.v04_hitl import income_statement as v04_income_statement


def _record(
    debit: str,
    credit: str,
    amount: float,
    *,
    status: str = "generated",
) -> dict[str, object]:
    return {
        "debit": debit,
        "credit": credit,
        "amount": amount,
        "status": status,
    }


def _purchase() -> list[dict[str, object]]:
    return [
        _record("原材料", "", 353982.30),
        _record("应交税费-应交增值税(进项)", "", 46017.70),
        _record("", "应付账款", 400000.00),
    ]


def _sale() -> list[dict[str, object]]:
    return [
        _record("银行存款", "", 300000.00),
        _record("", "主营业务收入", 265486.73),
        _record("", "应交税费-应交增值税(销项)", 34513.27),
    ]


def _workbook_values(blob: bytes) -> dict[str, float]:
    worksheet = load_workbook(io.BytesIO(blob), data_only=True).active
    values: dict[str, float] = {}
    for row in worksheet.iter_rows(min_row=5, max_col=2):
        label = row[0].value
        value = row[1].value
        if isinstance(label, str) and isinstance(value, (int, float)):
            values[label] = float(value)
    return values


@pytest.mark.parametrize(
    ("account", "category"),
    (
        ("固定资产清理", "other_assets"),
        ("累计折旧", "contra_assets"),
        ("应交税费-应交增值税(进项)", "taxes"),
        ("其他应收款-备用金", "other_assets"),
        ("资产处置损益", "asset_disposal_gain"),
        ("主营业务成本", "cost"),
        ("研发费用", "admin_expense"),
    ),
)
def test_classify_account_uses_canonical_statement_categories(
    account: str,
    category: str,
) -> None:
    assert classify_account(account) == category


def test_purchase_vat_is_balanced_and_does_not_create_phantom_profit() -> None:
    values = balance_sheet_values(_purchase())
    profit = income_statement_values(_purchase())

    assert values["inventory"] == 353982.30
    assert values["taxes"] == -46017.70
    assert values["cash"] == 0.00
    assert values["payables"] == 400000.00
    assert values["balance_check"] == 0.0
    assert profit["revenue"] == 0.0
    assert profit["net_profit"] == 0.0


def test_sales_vat_is_balanced_and_recognizes_revenue_once() -> None:
    values = balance_sheet_values(_sale())
    profit = income_statement_values(_sale())

    assert values["cash"] == 300000.00
    assert values["taxes"] == 34513.27
    assert values["retained_earnings"] == 265486.73
    assert values["balance_check"] == 0.0
    assert profit["revenue"] == 265486.73
    assert profit["net_profit"] == 265486.73


def test_asset_disposal_gain_and_loss_stay_out_of_operating_revenue() -> None:
    gain_entries = build_asset_disposal_entries(
        "卖旧车，收到转账45000元，原值80000，折旧50000。"
    ).entries
    loss_entries = build_asset_disposal_entries(
        "处置旧设备，收到转账10000元，原值80000，折旧50000。"
    ).entries

    gain_values = balance_sheet_values(gain_entries, statuses=None)
    gain_profit = income_statement_values(gain_entries, statuses=None)
    loss_values = balance_sheet_values(loss_entries, statuses=None)
    loss_profit = income_statement_values(loss_entries, statuses=None)

    assert gain_values["balance_check"] == 0.0
    assert gain_profit["revenue"] == 0.0
    assert gain_profit["asset_disposal_gain"] == 15000.00
    assert loss_values["balance_check"] == 0.0
    assert loss_profit["revenue"] == 0.0
    assert loss_profit["asset_disposal_gain"] == -20000.00


def test_income_tax_and_operating_expenses_feed_stepwise_profit() -> None:
    records = [
        *_sale(),
        _record("主营业务成本", "库存商品", 100000.00),
        _record("销售费用", "银行存款", 10000.00),
        _record("管理费用", "银行存款", 15000.00),
        _record("财务费用", "应付利息", 5000.00),
        _record("税金及附加", "应交税费", 3000.00),
        _record("所得税费用", "应交税费", 20000.00),
    ]
    profit = income_statement_values(records)

    assert profit["revenue"] == 265486.73
    assert profit["cost"] == 100000.00
    assert profit["operating_profit"] == 132486.73
    assert profit["net_profit"] == 112486.73


def test_status_filter_keeps_generated_default_and_v04_accepts_all_statuses() -> None:
    records = [
        _record("银行存款", "主营业务收入", 1000.00),
        _record("管理费用", "银行存款", 200.00, status="review_required"),
    ]

    generated = statement_figures(records, statuses={"generated"})[1]
    all_statuses = statement_figures(records, statuses=None)[1]

    assert generated["revenue"] == 1000.00
    assert generated["admin_expense"] == 0.00
    assert all_statuses["revenue"] == 1000.00
    assert all_statuses["admin_expense"] == 200.00


def test_trial_balance_groups_same_accounts_with_pandas() -> None:
    records = [
        _record("银行存款", "", 100.00),
        _record("银行存款", "", 250.50),
        _record("", "主营业务收入", 350.50),
    ]

    grouped = trial_balance(records)
    cash = grouped.loc[grouped["科目"] == "银行存款"].iloc[0]

    assert cash["借方发生额"] == 350.50
    assert cash["贷方发生额"] == 0.00


def test_v03_excel_and_v04_reports_use_the_same_canonical_values() -> None:
    records = [
        *_sale(),
        _record("主营业务成本", "库存商品", 100000.00),
        _record("管理费用", "银行存款", 5000.00),
    ]
    core_balance = balance_sheet_values(records, statuses={"generated"})
    core_income = income_statement_values(records, statuses={"generated"})

    v03_balance = _workbook_values(build_balance_sheet_xlsx(records))
    v03_income = _workbook_values(build_income_statement_xlsx(records))
    v04_balance = v04_balance_sheet(records).set_index("项目")["金额"].to_dict()
    v04_income = v04_income_statement(records).set_index("项目")["金额"].to_dict()

    assert v03_balance["货币资金"] == core_balance["cash"]
    assert v03_balance["存货"] == core_balance["inventory"]
    assert v03_balance["资产总计"] == core_balance["total_assets"]
    assert v03_income["一、营业收入"] == core_income["revenue"]
    assert v03_income["三、净利润"] == core_income["net_profit"]
    assert v04_balance["货币资金"] == core_balance["cash"]
    assert v04_balance["资产总计"] == core_balance["total_assets"]
    assert v04_income["一、营业收入"] == core_income["revenue"]
    assert v04_income["三、净利润"] == core_income["net_profit"]


def test_statement_rows_and_values_remain_the_same_shape() -> None:
    records = [*_purchase(), *_sale()]
    balance = balance_sheet_rows(records)
    income = income_statement_rows(records)

    assert balance[0] == ("资产类", None)
    assert ("资产总计", balance_sheet_values(records)["total_assets"]) in balance
    assert income[-1][0] == "三、净利润"
