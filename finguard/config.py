"""Configuration objects for FinGuard.

The thresholds live in one place so audit teams can align the system with
their materiality policy without editing detection logic.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class IngestionConfig:
    """Controls input safety and cleaning behaviour."""

    max_file_mb: float = 25.0
    max_rows: int = 200_000
    default_missing_user: str = "未知制单人"
    default_missing_summary: str = "未填写摘要"


@dataclass(frozen=True)
class RiskConfig:
    """Transparent, additive risk-matrix policy.

    Each evidence layer can add points once per voucher. Multiple findings in
    the same layer remain visible as separate labels, avoiding an unstable
    score caused by duplicating one evidence category.
    """

    rule_points: float = 30.0
    statistical_points: float = 40.0
    cross_validation_points: float = 30.0
    high_risk_threshold: float = 60.0
    medium_risk_threshold: float = 30.0

    extreme_mean_multiplier: float = 3.0
    near_duplicate_pct: float = 0.01
    statistical_method: str = "iqr"
    iqr_multiplier: float = 3.0
    zscore_threshold: float = 3.0
    minimum_statistical_sample: int = 8

    night_start_hour: int = 0
    night_end_hour: int = 5

    vague_summary_terms: tuple[str, ...] = (
        "办公用品",
        "劳保",
        "劳保用品",
        "招待",
        "业务招待",
        "礼品",
        "餐饮",
        "会议费",
        "培训费",
        "差旅",
        "差旅费",
        "耗材",
        "维修",
        "咨询费",
    )
    detail_markers: tuple[str, ...] = (
        "发票",
        "明细",
        "数量",
        "单价",
        "清单",
        "合同",
        "项目",
        "部门",
        "事由",
        "单号",
        "订单",
        "供应商",
        "领用",
        "出库",
        "入库",
    )
    vague_summary_max_chars: int = 16
    vague_amount_quantile: float = 0.90

    cross_min_amount: float = 1_000.0
    cross_rounding_pct: float = 0.001
    cross_rounding_floor: float = 1.0
    cross_min_vouchers: int = 2

    bulk_procurement_terms: tuple[str, ...] = (
        "钢材",
        "设备",
        "服务器",
        "生产材料",
        "工程材料",
        "大型采购",
        "批量采购",
    )
    bulk_procurement_min_amount: float = 10_000.0
    bulk_procurement_min_score: float = 40.0
    bulk_procurement_score_cap: float = 45.0
    same_value_split_min_count: int = 3
    same_value_split_score: float = 80.0
    statistical_trim_quantile: float = 0.05
    asset_price_ranges: tuple[tuple[str, float, float], ...] = (
        ("割草机", 1_000.0, 10_000.0),
        ("推车", 1_000.0, 10_000.0),
        ("服务器", 1_000.0, 10_000.0),
        ("生产设备", 1_000.0, 10_000.0),
        ("设备", 1_000.0, 10_000.0),
        ("电脑", 1_000.0, 10_000.0),
        ("办公桌", 1_000.0, 10_000.0),
        ("办公椅", 1_000.0, 10_000.0),
    )
    asset_whitelist_score: float = 45.0
    asset_frequency_score: float = 90.0
    payroll_terms: tuple[str, ...] = ("工资", "奖金")
    payroll_department_terms: tuple[str, ...] = (
        "财务部",
        "行政部",
        "人力资源部",
        "人事部",
        "薪酬组",
    )
    payroll_individual_alert_amount: float = 50_000.0
    payroll_department_min_amount: float = 1_000.0
    payroll_department_max_amount: float = 100_000.0
    payroll_fraud_score: float = 90.0
    payroll_department_score: float = 45.0
    feedback_score_reduction: float = 20.0
    feedback_similarity_threshold: float = 0.75
    feedback_amount_tolerance: float = 0.10
    frequency_terms: tuple[str, ...] = (
        "割草机",
        "推车",
        "服务器",
        "生产设备",
        "设备",
        "园艺手套",
        "花园水管",
        "园艺剪",
        "绿篱修剪机",
        "吹叶机",
        "耙子",
        "铁锹",
        "洒水壶",
        "打印纸",
        "办公桌",
        "办公椅",
        "钢材",
        "电脑",
    )
    frequency_window_days: int = 30
    frequency_min_count: int = 3
    frequency_anomaly_score: float = 80.0

    #: Scores at the strict threshold remain medium risk; only scores above
    #: it trigger the LLM layer.
    ai_trigger_threshold: float = 60.0

    def validate(self) -> None:
        if not 0 <= self.medium_risk_threshold < self.high_risk_threshold <= 100:
            raise ValueError("中高风险阈值必须满足 0 ≤ 中风险 < 高风险 ≤ 100")
        if not 0 <= self.ai_trigger_threshold <= 100:
            raise ValueError("ai_trigger_threshold 必须位于 [0, 100]")
        if not 0 < self.rule_points <= 100:
            raise ValueError("rule_points 必须在 (0, 100] 内")
        if not 0 < self.statistical_points <= 100:
            raise ValueError("statistical_points 必须在 (0, 100] 内")
        if not 0 <= self.cross_validation_points <= 100:
            raise ValueError("cross_validation_points 必须在 [0, 100] 内")
        if self.statistical_method not in {"iqr", "zscore"}:
            raise ValueError("statistical_method 仅支持 'iqr' 或 'zscore'")
        if self.minimum_statistical_sample < 4:
            raise ValueError("minimum_statistical_sample 不能小于 4")
        if not 0 <= self.night_start_hour < self.night_end_hour <= 24:
            raise ValueError("凌晨时间窗口配置无效")
        if not 0 < self.near_duplicate_pct < 1:
            raise ValueError("near_duplicate_pct 必须位于 (0, 1) 内")
        if self.bulk_procurement_min_amount <= 0:
            raise ValueError("bulk_procurement_min_amount 必须大于 0")
        if not 40 <= self.bulk_procurement_min_score <= self.bulk_procurement_score_cap:
            raise ValueError("bulk_procurement_min_score 必须位于 40 到封顶分之间")
        if not 30 <= self.bulk_procurement_score_cap <= 50:
            raise ValueError("bulk_procurement_score_cap 必须位于 30–50")
        if self.same_value_split_min_count < 3:
            raise ValueError("same_value_split_min_count 不能小于 3")
        if not 0 <= self.statistical_trim_quantile < 0.5:
            raise ValueError("statistical_trim_quantile 必须位于 [0, 0.5) 内")
        if self.frequency_window_days < 1:
            raise ValueError("frequency_window_days 必须大于 0")
        if self.frequency_min_count < 3:
            raise ValueError("frequency_min_count 不能小于 3")
        if self.payroll_individual_alert_amount <= 0:
            raise ValueError("payroll_individual_alert_amount 必须大于 0")
        if self.payroll_department_min_amount >= self.payroll_department_max_amount:
            raise ValueError("常规薪酬区间上界必须大于下界")
        if not 0 <= self.feedback_score_reduction <= 100:
            raise ValueError("feedback_score_reduction 必须位于 [0, 100]")


RULE_PROFILE_FIELDS = (
    "rule_points",
    "statistical_points",
    "cross_validation_points",
    "high_risk_threshold",
    "medium_risk_threshold",
    "extreme_mean_multiplier",
    "near_duplicate_pct",
    "statistical_method",
    "iqr_multiplier",
    "zscore_threshold",
    "minimum_statistical_sample",
    "night_start_hour",
    "night_end_hour",
    "bulk_procurement_min_amount",
    "bulk_procurement_min_score",
    "bulk_procurement_score_cap",
    "same_value_split_min_count",
    "same_value_split_score",
    "statistical_trim_quantile",
    "asset_whitelist_score",
    "asset_frequency_score",
    "payroll_individual_alert_amount",
    "payroll_department_min_amount",
    "payroll_department_max_amount",
    "payroll_fraud_score",
    "payroll_department_score",
    "feedback_score_reduction",
    "frequency_window_days",
    "frequency_min_count",
    "frequency_anomaly_score",
    "ai_trigger_threshold",
)


def risk_config_profile(config: RiskConfig) -> dict[str, Any]:
    return {field: getattr(config, field) for field in RULE_PROFILE_FIELDS}


def risk_config_from_profile(values: dict[str, Any]) -> RiskConfig:
    defaults = RiskConfig()
    overrides: dict[str, Any] = {}
    for field in RULE_PROFILE_FIELDS:
        if field not in values:
            continue
        default = getattr(defaults, field)
        value = values[field]
        if field == "statistical_method":
            overrides[field] = str(value)
        elif isinstance(default, int):
            overrides[field] = int(value)
        else:
            overrides[field] = float(value)
    config = replace(defaults, **overrides)
    config.validate()
    return config
