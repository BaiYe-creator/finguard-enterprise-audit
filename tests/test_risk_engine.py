from __future__ import annotations

from pathlib import Path

import pandas as pd

from finguard.config import RiskConfig
from finguard.data_ingestion import DataIngestionService
from finguard.demo import build_demo_dataframe, demo_csv_bytes
from finguard.risk_engine import RiskEngine


def analyzed_demo() -> pd.DataFrame:
    ingestion = DataIngestionService().load_and_clean(
        demo_csv_bytes(),
        filename="demo.csv",
    )
    return RiskEngine(RiskConfig()).evaluate(ingestion.clean_data).data


def test_demo_activates_all_three_layers() -> None:
    data = analyzed_demo()
    high = data.loc[data["voucher_id"].str.startswith("PZ-20261003")]
    assert len(high) == 2
    assert high["Risk_Score"].eq(100).all()
    assert high["is_extreme_amount"].all()
    assert high["is_near_duplicate"].all()
    assert high["is_statistical_outlier"].all()
    assert high["is_cross_validated"].all()
    assert high["Risk_Level"].eq("高风险").all()
    assert high["ai_advice"].eq("").all()


def test_weekend_rule_is_medium_and_not_high() -> None:
    data = analyzed_demo()
    control = data.loc[data["voucher_id"].eq("PZ-20260919-9001")].iloc[0]
    assert bool(control["is_weekend"])
    assert control["Risk_Score"] == 30
    assert control["Risk_Level"] == "中风险"


def test_near_duplicates_are_detected_on_sorted_adjacent_amounts() -> None:
    frame = pd.DataFrame(
        {
            "voucher_id": ["A", "B", "C"],
            "date": pd.to_datetime(["2026-09-01 09:00", "2026-09-01 10:00", "2026-09-01 11:00"]),
            "user": ["甲", "甲", "甲"],
            "summary": ["明细 A", "明细 B", "明细 C"],
            "amount": [1000.0, 1005.0, 3000.0],
            "debit": [1000.0, 1005.0, 3000.0],
            "credit": [0.0, 0.0, 0.0],
            "_has_explicit_time": [True, True, True],
        }
    )
    flags = RiskEngine()._near_duplicate_flags(frame)
    assert flags.tolist() == [True, True, False]


def test_demo_raw_mapping_is_not_presupposed() -> None:
    raw = build_demo_dataframe()
    assert "借" in raw.columns
    assert "经办人" in raw.columns


def test_realistic_dirty_csv_split_rule_refund_and_statistical_exclusion() -> None:
    path = Path(__file__).resolve().parents[1] / "sample_data" / "dirty_vouchers_100.csv"
    ingestion = DataIngestionService().load_path(path)
    assessment = RiskEngine(RiskConfig()).evaluate(ingestion.clean_data)
    data = assessment.data

    assert assessment.metrics["total_count"] == 99
    assert assessment.metrics["analyzed_count"] == 98
    assert assessment.metrics["refund_count"] == 1
    assert assessment.distribution["n"] == 98

    refund = data.loc[data["voucher_id"].eq("PZ-018")].iloc[0]
    assert refund["Risk_Score"] == 0
    assert refund["Risk_Level"] == "退款/红字冲销"
    assert refund["ai_status"] == "无需AI（退款/冲销）"
    assert "不参与常规风险评分" in refund["Risk_Features"]

    split_groups = [
        ("2026-10-11", "王五"),
        ("2026-11-09", "孙七"),
    ]
    expected_ids: set[str] = set()
    for day, user in split_groups:
        group = data.loc[
            data["date"].dt.strftime("%Y-%m-%d").eq(day)
            & data["user"].eq(user)
            & data["amount"].eq(4999)
        ]
        assert len(group) == 3
        assert group["is_split_reimbursement"].all()
        assert group["Risk_Score"].eq(80).all()
        assert group["Risk_Level"].eq("高风险").all()
        assert group["split_reimbursement_count"].eq(3).all()
        assert group["Risk_Features"].str.contains("拆分报销").all()
        expected_ids.update(group["voucher_id"])

    assert len(expected_ids) == 6

    steel = data.loc[data["voucher_id"].eq("PZ-004")].iloc[0]
    assert steel["is_bulk_procurement"]
    assert steel["Risk_Score"] == 45
    assert steel["Risk_Level"] == "中风险"
    assert "大额业务支出，建议核实对应的采购合同、入库单及发票明细" in steel["Risk_Features"]
    assert steel["ai_status"] == "建议人工复核（大宗采购）"

    equal_value_group = data.loc[data["voucher_id"].isin(["PZ-032", "PZ-033", "PZ-034", "PZ-035"])]
    assert len(equal_value_group) == 4
    assert equal_value_group["is_same_value_split"].all()
    assert equal_value_group["same_value_split_count"].eq(4).all()
    assert equal_value_group["Risk_Score"].eq(80).all()
    assert equal_value_group["Risk_Level"].eq("高风险").all()
    assert (
        equal_value_group["Risk_Features"]
        .str.contains("同一天内多笔等额报销，疑似拆分逃避审批")
        .all()
    )


def test_real_sap_asset_whitelist_and_frequency_anomaly() -> None:
    path = Path(__file__).resolve().parents[1] / "Real_Data_Clean.csv"
    ingestion = DataIngestionService().load_path(path)
    assessment = RiskEngine(RiskConfig()).evaluate(ingestion.clean_data)
    data = assessment.data

    max_mower = data.loc[data["voucher_id"].eq("a_30")].iloc[0]
    david_mower = data.loc[data["voucher_id"].eq("a_51")].iloc[0]
    for mower in (max_mower, david_mower):
        assert mower["is_asset_whitelist"]
        assert not mower["is_extreme_amount"]
        assert not mower["is_statistical_outlier"]
        assert mower["Risk_Score"] == 45
        assert mower["Risk_Level"] == "中风险"
        assert "大宗资产采购，建议核验入库单" in mower["Risk_Features"]

    frequency_ids = {"a_56", "a_23", "a_40"}
    frequency_group = data.loc[data["voucher_id"].isin(frequency_ids)]
    assert len(frequency_group) == 3
    assert frequency_group["is_frequency_anomaly"].all()
    assert frequency_group["frequency_keyword"].eq("花园水管").all()
    assert frequency_group["Risk_Score"].eq(80).all()
    assert frequency_group["Risk_Level"].eq("高风险").all()
    assert (
        frequency_group["Risk_Features"]
        .str.contains("短期内高频采购同类物资，疑似拆分采购或虚报需求")
        .all()
    )


def test_statistical_baseline_uses_top_and_bottom_five_percent_trimming() -> None:
    amounts = pd.Series([100.0] * 50 + [120.0] * 40 + [1_000_000.0] * 10)
    engine = RiskEngine(RiskConfig())
    warnings: list[str] = []
    distribution = engine._amount_distribution(amounts, warnings)
    assert distribution["n"] == 100
    assert distribution["trimmed_n"] < 100
    assert distribution["mean"] < amounts.mean()


def test_payroll_cross_validation_detects_individual_and_allows_department() -> None:
    csv = """凭证号,日期,制单人,摘要,借方,贷方
SAL-1,2026-10-01,张三,发放工资,60000,0
SAL-2,2026-10-01,财务部,全公司工资,30000,0
"""
    ingestion = DataIngestionService().load_and_clean(
        csv.encode("utf-8-sig"),
        filename="payroll.csv",
    )
    data = RiskEngine(RiskConfig()).evaluate(ingestion.clean_data).data

    individual = data.loc[data["voucher_id"].eq("SAL-1")].iloc[0]
    department = data.loc[data["voucher_id"].eq("SAL-2")].iloc[0]
    assert individual["is_payroll_fraud"]
    assert individual["Risk_Score"] == 90
    assert individual["Risk_Level"] == "高风险"
    assert "个人提交大额薪酬，疑似虚构员工套现" in individual["Risk_Features"]
    assert department["is_payroll_department_regular"]
    assert department["Risk_Score"] == 45
    assert department["Risk_Level"] == "中风险"


def test_three_asset_purchases_in_30_days_trigger_90_score() -> None:
    csv = """凭证号,日期,制单人,摘要,金额
ASSET-1,2026-03-01,Max,采购割草机｜商品成本,2000
ASSET-2,2026-03-10,Max,采购割草机｜商品成本,2500
ASSET-3,2026-03-20,Max,采购割草机｜商品成本,3000
"""
    ingestion = DataIngestionService().load_and_clean(
        csv.encode("utf-8-sig"),
        filename="asset-frequency.csv",
    )
    data = RiskEngine(RiskConfig()).evaluate(ingestion.clean_data).data
    assert data["is_asset_whitelist"].all()
    assert data["is_asset_frequency_anomaly"].all()
    assert data["Risk_Score"].eq(90).all()
    assert data["Risk_Level"].eq("高风险").all()
    assert (
        data["Risk_Features"]
        .str.contains("短期内高频采购同类大额资产，高度疑似虚构采购套取资金")
        .all()
    )
