from __future__ import annotations

import io

import pytest
from openpyxl import load_workbook

from finguard.v04_hitl import (
    balance_sheet,
    balance_totals,
    editable_frame,
    income_statement,
    recalculate_reports,
    report_xlsx,
    sanitize_editor_dataframe,
    source_entries,
)


def _entries() -> list[dict]:
    return [
        {
            "entry_id": "BZ-0001-01-a",
            "business": "销售产品",
            "debit": "银行存款",
            "credit": "",
            "amount": 1130.0,
            "description": "含税销售额",
            "status": "generated",
        },
        {
            "entry_id": "BZ-0001-02-b",
            "business": "销售产品",
            "debit": "",
            "credit": "主营业务收入",
            "amount": 1000.0,
            "description": "不含税收入",
            "status": "generated",
        },
        {
            "entry_id": "BZ-0001-03-c",
            "business": "销售产品",
            "debit": "",
            "credit": "应交税费-应交增值税(销项)",
            "amount": 130.0,
            "description": "销项税额",
            "status": "generated",
        },
    ]


def test_source_entries_have_unique_ids() -> None:
    history = [
        {
            "created_at": "2026-10-30T10:00:00",
            "line": 1,
            "entry_order": 1,
            "business": "销售产品",
            "debit": "银行存款",
            "credit": "",
            "amount": 100,
            "description": "借方",
            "status": "generated",
        },
        {
            "created_at": "2026-10-30T10:00:00",
            "line": 1,
            "entry_order": 2,
            "business": "销售产品",
            "debit": "",
            "credit": "主营业务收入",
            "amount": 100,
            "description": "贷方",
            "status": "generated",
        },
    ]

    entries = source_entries(history)

    assert len({entry["entry_id"] for entry in entries}) == 2


def test_editor_sanitizes_amounts_and_recalculates_reports() -> None:
    edited = editable_frame(_entries())
    edited["金额"] = edited["金额"].astype(object)
    edited.loc[0, "金额"] = "1,130.00"
    entries, invalid_amounts = sanitize_editor_dataframe(edited)

    assert invalid_amounts == 0
    assert balance_totals(entries)[2]
    reports = recalculate_reports(entries)
    assert (
        reports["balance_sheet"].loc[reports["balance_sheet"]["项目"] == "平衡校验", "金额"].iloc[0]
        == 0.0
    )
    assert (
        reports["income_statement"]
        .loc[reports["income_statement"]["项目"] == "一、营业收入", "金额"]
        .iloc[0]
        == 1000.0
    )


def test_non_numeric_amount_breaks_balance_and_is_blocked() -> None:
    edited = editable_frame(_entries())
    edited["金额"] = edited["金额"].astype(object)
    edited.loc[1, "金额"] = "一千元"
    entries, invalid_amounts = sanitize_editor_dataframe(edited)
    debit_total, credit_total, balanced = balance_totals(entries)

    assert invalid_amounts == 1
    assert debit_total == 1130.0
    assert credit_total == 130.0
    assert not balanced


def test_unbalanced_report_values_are_detectable() -> None:
    entries = _entries()
    entries[2]["amount"] = 100.0

    debit_total, credit_total, balanced = balance_totals(entries)

    assert debit_total == 1130.0
    assert credit_total == 1100.0
    assert not balanced


def test_reports_are_balanced_and_downloadable_xlsx() -> None:
    entries, _ = sanitize_editor_dataframe(editable_frame(_entries()))
    sheet = balance_sheet(entries)
    income = income_statement(entries)

    assert (
        sheet.loc[sheet["项目"] == "资产总计", "金额"].iloc[0]
        == sheet.loc[sheet["项目"] == "负债及所有者权益总计", "金额"].iloc[0]
    )
    assert sheet.loc[sheet["项目"] == "平衡校验", "金额"].iloc[0] == 0.0
    assert income.loc[income["项目"] == "三、净利润", "金额"].iloc[0] == 1000.0

    content = report_xlsx(sheet, "资产负债表")
    worksheet = load_workbook(io.BytesIO(content)).active
    assert content.startswith(b"PK")
    assert worksheet["A1"].value == "资产负债表"
    assert any(cell.value == "资产总计" for row in worksheet.iter_rows() for cell in row)


def test_duplicate_entry_id_is_rejected() -> None:
    edited = editable_frame(_entries())
    edited.loc[1, "entry_id"] = edited.loc[0, "entry_id"]

    with pytest.raises(ValueError, match="entry_id 重复"):
        sanitize_editor_dataframe(edited)


def test_report_groupby_combines_same_accounts() -> None:
    entries = _entries()
    entries.append(
        {
            "entry_id": "BZ-0002-01-d",
            "business": "销售产品",
            "debit": "银行存款",
            "credit": "",
            "amount": 2260.0,
            "description": "第二笔销售额",
            "status": "generated",
        }
    )
    entries.append(
        {
            "entry_id": "BZ-0002-02-e",
            "business": "销售产品",
            "debit": "",
            "credit": "主营业务收入",
            "amount": 2260.0,
            "description": "第二笔收入",
            "status": "generated",
        }
    )

    reports = recalculate_reports(entries)
    trial = reports["trial_balance"]

    assert trial.loc[trial["科目"] == "银行存款", "借方发生额"].iloc[0] == 3390.0
    assert trial.loc[trial["科目"] == "主营业务收入", "贷方发生额"].iloc[0] == 3260.0
