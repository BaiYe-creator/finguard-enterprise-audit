from __future__ import annotations

import pandas as pd

from scripts.clean_sap_journal import (
    clean_sap_journal,
    parse_amount,
    translate_summary,
)


def sample_journal() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "posting_id": "a_1",
                "document_date": "2023-05-31",
                "user": "David",
                "gl_account": "3400",
                "gl_account_name": "Cost of merchandise, 19% input tax",
                "amount": "1,112.42元",
                "cd_flag": "D",
                "text": "Lawn Mower",
            },
            {
                "posting_id": "a_1",
                "document_date": "2023-05-31",
                "user": "David",
                "gl_account": "1576",
                "gl_account_name": "Deductible input tax, 19%",
                "amount": "211.36",
                "cd_flag": "D",
                "text": "Lawn Mower",
            },
            {
                "posting_id": "a_1",
                "document_date": "2023-05-31",
                "user": "David",
                "gl_account": "1600",
                "gl_account_name": "Trade payables",
                "amount": "1,323.78",
                "cd_flag": "C",
                "text": "Lawn Mower",
            },
        ]
    )


def test_cost_account_mode_outputs_five_columns() -> None:
    cleaned = clean_sap_journal(sample_journal(), amount_mode="cost_account")
    assert cleaned.columns.tolist() == ["voucher_id", "date", "user", "summary", "amount"]
    assert len(cleaned) == 1
    assert cleaned.loc[0, "voucher_id"] == "a_1"
    assert cleaned.loc[0, "amount"] == 1_112.42
    assert cleaned.loc[0, "summary"] == "采购割草机｜商品成本"


def test_debit_total_excludes_credit_line() -> None:
    cleaned = clean_sap_journal(sample_journal(), amount_mode="debit_total")
    assert cleaned.loc[0, "amount"] == 1_323.78


def test_amount_and_summary_helpers() -> None:
    assert parse_amount("¥ 1,2#3.4元") == 123.4
    assert (
        translate_summary(
            "Rake",
            "3400",
            "Cost of merchandise, 19% input tax",
        )
        == "采购耙子｜商品成本"
    )
