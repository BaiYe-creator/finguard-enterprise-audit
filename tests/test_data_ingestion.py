from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import pytest

from finguard.data_ingestion import (
    CANONICAL_COLUMNS,
    DataIngestionError,
    DataIngestionService,
    _parse_amount,
    rename_columns,
)


def test_rename_columns_handles_aliases_and_currency() -> None:
    raw = pd.DataFrame(
        {
            "凭证编号": ["V-001"],
            "制单日期": ["2026-09-01"],
            "经办人": ["张三"],
            "说明": ["办公用品｜发票 FP-001"],
            "借": ["¥1,234.50"],
            "贷": ["0"],
        }
    )
    result = rename_columns(raw)
    assert set(CANONICAL_COLUMNS).issubset(result.columns)
    assert result.loc[0, "voucher_id"] == "V-001"
    assert result.loc[0, "user"] == "张三"
    assert result.loc[0, "summary"] == "办公用品｜发票 FP-001"


def test_service_cleans_amounts_dates_blank_and_summary_rows() -> None:
    csv = """凭证号,日期,时间,制单人,摘要,借方,贷方
V1,2026-09-01,09:20,张三,采购发票 FP-1,"¥1,234.50",0
,,,,,,
V2,2026-09-02,,李四,服务费,700.00,0
合计,,,,本期合计,"¥1,934.50",0
V3,not-a-date,10:00,王五,差旅,"¥80.00",0
"""
    result = DataIngestionService().load_and_clean(
        csv.encode("utf-8-sig"),
        filename="vouchers.csv",
    )
    assert len(result.clean_data) == 2
    assert result.clean_data["amount"].sum() == pytest.approx(1_934.50)
    assert result.report["blank_rows_removed"] == 1
    assert result.report["summary_rows_removed"] == 1
    assert result.report["invalid_dates_removed"] == 1
    assert result.clean_data["_has_explicit_time"].tolist() == [True, False]


def test_generic_amount_with_direction_is_split() -> None:
    raw = pd.DataFrame(
        {
            "日期": ["2026-09-01", "2026-09-02"],
            "制单人": ["甲", "乙"],
            "摘要": ["费用发票 A", "费用发票 B"],
            "金额": ["1,200.00", "900.00"],
            "借贷方向": ["借", "贷"],
        }
    )
    csv = raw.to_csv(index=False).encode("utf-8-sig")
    result = DataIngestionService().load_and_clean(csv, filename="generic.csv")
    first = result.clean_data.loc[result.clean_data["user"] == "甲"].iloc[0]
    second = result.clean_data.loc[result.clean_data["user"] == "乙"].iloc[0]
    assert first["debit"] == pytest.approx(1_200.0)
    assert first["credit"] == 0
    assert second["credit"] == pytest.approx(900.0)
    assert second["debit"] == 0


def test_missing_required_field_has_actionable_error() -> None:
    raw = pd.DataFrame({"日期": ["2026-09-01"], "摘要": ["x"], "借方": [100]})
    csv = raw.to_csv(index=False).encode("utf-8-sig")
    with pytest.raises(DataIngestionError, match="制单人"):
        DataIngestionService().load_and_clean(csv, filename="bad.csv")


def test_xlsx_ingestion_reads_selected_sheet() -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        path = Path(directory) / "vouchers.xlsx"
        frame = pd.DataFrame(
            {
                "凭证号": ["XL-1", "XL-2"],
                "记账日期": ["2026-09-01", "2026-09-02"],
                "经办人": ["张三", "李四"],
                "说明": ["配件采购｜发票 F-1", "差旅住宿｜项目 P-2"],
                "借": ["1,280.00", 760],
                "贷": [0, 0],
            }
        )
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            pd.DataFrame({"说明": ["这是说明页"]}).to_excel(
                writer,
                sheet_name="说明",
                index=False,
            )
            frame.to_excel(writer, sheet_name="凭证", index=False)
        result = DataIngestionService().load_path(path, sheet_name="凭证")
        assert len(result.clean_data) == 2
        assert result.clean_data["amount"].sum() == pytest.approx(2_040.0)


def test_amount_parser_removes_currency_commas_and_noise() -> None:
    assert _parse_amount("¥ 1,2#3.4元") == pytest.approx(123.4)
    assert _parse_amount("金额：-5,000.00元") == pytest.approx(-5_000.0)
    assert pd.isna(_parse_amount("没有金额"))


def test_summary_keyword_with_blank_amount_is_removed() -> None:
    csv = """凭证号,日期,制单人,摘要,借方,贷方
SUMMARY-1,,,费用汇总,,
PZ-1,2026-10-08,张三,采购A4纸10箱,1200,0
"""
    result = DataIngestionService().load_and_clean(
        csv.encode("utf-8-sig"),
        filename="summary.csv",
    )
    assert len(result.clean_data) == 1
    assert result.report["summary_rows_removed"] == 1


def test_realistic_dirty_csv_survives_and_marks_refund() -> None:
    path = Path(__file__).resolve().parents[1] / "sample_data" / "dirty_vouchers_100.csv"
    result = DataIngestionService().load_path(path)
    assert len(result.clean_data) == 99
    assert result.report["raw_rows"] == 104
    assert result.report["summary_rows_removed"] == 4
    assert result.report["zero_amount_removed"] == 1
    assert result.report["refund_count"] == 1

    refund = result.clean_data.loc[result.clean_data["voucher_id"].eq("PZ-018")].iloc[0]
    assert refund["is_refund"]
    assert refund["transaction_type"] == "退款/红字冲销"
    assert refund["debit"] == pytest.approx(-5_000.0)
