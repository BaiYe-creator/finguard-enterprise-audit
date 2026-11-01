"""Explainable multi-layer financial anomaly scoring."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from .config import RiskConfig
from .feedback_store import FeedbackStore

try:
    import holidays as holidays_lib
except ImportError:  # The rules still run, with a visible holiday-engine warning.
    holidays_lib = None


class RiskEngineError(ValueError):
    """Raised when risk scoring receives an invalid cleaned dataset."""


@dataclass
class RiskAssessment:
    data: pd.DataFrame
    metrics: dict[str, Any] = field(default_factory=dict)
    distribution: dict[str, float | str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _join_labels(labels: list[str]) -> str:
    return "；".join(dict.fromkeys(label for label in labels if label))


class RiskEngine:
    """Apply hard rules, statistical outliers, and contextual cross-checks.

    Layer scores are additive and capped at 100. A layer contributes at most
    once per voucher; every individual finding remains separately auditable.
    """

    def __init__(
        self,
        config: RiskConfig | None = None,
        feedback_store: FeedbackStore | None = None,
    ) -> None:
        self.config = config or RiskConfig()
        self.feedback_store = feedback_store
        self.config.validate()

    def evaluate(self, frame: pd.DataFrame) -> RiskAssessment:
        required = {
            "voucher_id",
            "date",
            "user",
            "summary",
            "debit",
            "credit",
            "amount",
            "is_refund",
            "_has_explicit_time",
        }
        missing = sorted(required - set(frame.columns))
        if missing:
            raise RiskEngineError(f"风险引擎缺少字段：{', '.join(missing)}")
        if frame.empty:
            raise RiskEngineError("没有可评分的凭证")

        data = frame.copy().reset_index(drop=True)
        data["date"] = pd.to_datetime(data["date"], errors="coerce")
        if data["date"].isna().any():
            raise RiskEngineError("风险引擎收到含无效日期的数据，请先执行数据清洗")
        data["amount"] = pd.to_numeric(data["amount"], errors="coerce").abs()
        if data["amount"].isna().any() or (data["amount"] <= 0).any():
            raise RiskEngineError("风险引擎收到无效金额，请先执行数据清洗")

        warnings: list[str] = []
        refund_mask = data["is_refund"].fillna(False).astype(bool)
        active_mask = ~refund_mask
        if not active_mask.any():
            warnings.append("本次数据全部为退款/红字冲销，常规风险统计未启用。")

        active_data = data.loc[active_mask].copy()
        amount_stats = self._amount_distribution(active_data["amount"], warnings)

        weekend = data["date"].dt.dayofweek.ge(5) & active_mask
        holiday, holiday_note = self._holiday_flags(data["date"])
        if holiday_note:
            warnings.append(holiday_note)
        holiday = holiday & active_mask
        night = (
            data["_has_explicit_time"].fillna(False).astype(bool)
            & data["date"].dt.hour.ge(self.config.night_start_hour)
            & data["date"].dt.hour.lt(self.config.night_end_hour)
            & active_mask
        )

        mean_amount = float(amount_stats["mean"])
        extreme = data["amount"].gt(mean_amount * self.config.extreme_mean_multiplier) & active_mask
        near_duplicate = pd.Series(False, index=data.index, dtype=bool)
        if not active_data.empty:
            subset_flags = self._near_duplicate_flags(active_data)
            near_duplicate.loc[subset_flags.index] = subset_flags
        vague = pd.Series(False, index=data.index, dtype=bool)
        if not active_data.empty:
            subset_flags = self._vague_summary_flags(active_data, amount_stats)
            vague.loc[subset_flags.index] = subset_flags

        split_reimbursement, split_counts = self._split_reimbursement_flags(
            data,
            active_mask,
        )
        same_value_split, same_value_counts = self._same_value_split_flags(
            data,
            active_mask,
        )
        bulk_procurement = self._bulk_procurement_flags(data, active_mask)
        asset_whitelist, asset_keywords = self._asset_price_whitelist_flags(
            data,
            active_mask,
        )
        frequency_anomaly, frequency_keywords, frequency_counts = self._frequency_anomaly_flags(
            data, active_mask
        )
        asset_terms = {term for term, _, _ in self.config.asset_price_ranges}
        asset_frequency_anomaly = frequency_anomaly & frequency_keywords.isin(asset_terms)
        payroll, payroll_fraud, payroll_department_regular = self._payroll_flags(
            data,
            active_mask,
        )
        statistical = pd.Series(False, index=data.index, dtype=bool)
        if not active_data.empty:
            subset_flags, statistical_meta = self._statistical_outlier_flags(
                active_data["amount"],
                amount_stats,
                warnings,
            )
            statistical.loc[subset_flags.index] = subset_flags
        else:
            statistical_meta = {
                "method": "未启用",
                "method_label": "无可分析凭证",
                "lower": np.nan,
                "upper": np.nan,
                "per_row": pd.Series("", index=data.index, dtype="object"),
            }
        cross = pd.Series(False, index=data.index, dtype=bool)
        if not active_data.empty:
            subset_cross = self._cross_validation_flags(
                active_data,
                weekend.loc[active_mask],
                night.loc[active_mask],
                amount_stats,
            )
            cross.loc[subset_cross.index] = subset_cross

        extreme = extreme & ~asset_whitelist
        statistical = statistical & ~asset_whitelist
        if isinstance(statistical_meta["per_row"], pd.Series):
            statistical_meta["per_row"] = statistical_meta["per_row"].copy()
            statistical_meta["per_row"].loc[asset_whitelist] = ""

        rule_any = (
            extreme
            | weekend
            | holiday
            | night
            | near_duplicate
            | vague
            | split_reimbursement
            | same_value_split
            | bulk_procurement
            | asset_whitelist
            | frequency_anomaly
            | payroll
        )

        data["is_extreme_amount"] = extreme.to_numpy()
        data["is_near_duplicate"] = near_duplicate.to_numpy()
        data["is_weekend"] = weekend.to_numpy()
        data["is_holiday"] = holiday.to_numpy()
        data["is_night"] = night.to_numpy()
        data["is_vague_high_amount"] = vague.to_numpy()
        data["is_statistical_outlier"] = statistical.to_numpy()
        data["is_cross_validated"] = cross.to_numpy()
        data["is_split_reimbursement"] = split_reimbursement.to_numpy()
        data["split_reimbursement_count"] = split_counts.to_numpy()
        data["is_same_value_split"] = same_value_split.to_numpy()
        data["same_value_split_count"] = same_value_counts.to_numpy()
        data["is_bulk_procurement"] = bulk_procurement.to_numpy()
        data["is_asset_whitelist"] = asset_whitelist.to_numpy()
        data["asset_keyword"] = asset_keywords.to_numpy()
        data["is_frequency_anomaly"] = frequency_anomaly.to_numpy()
        data["frequency_keyword"] = frequency_keywords.to_numpy()
        data["frequency_window_count"] = frequency_counts.to_numpy()
        data["is_asset_frequency_anomaly"] = asset_frequency_anomaly.to_numpy()
        data["is_payroll"] = payroll.to_numpy()
        data["is_payroll_fraud"] = payroll_fraud.to_numpy()
        data["is_payroll_department_regular"] = payroll_department_regular.to_numpy()

        data["rule_flags"] = self._build_rule_labels(data)
        data["rule_score"] = np.where(rule_any, self.config.rule_points, 0.0)
        data["statistical_score"] = np.where(
            statistical,
            self.config.statistical_points,
            0.0,
        )
        data["cross_validation_score"] = np.where(
            cross,
            self.config.cross_validation_points,
            0.0,
        )
        additive_score = (
            data["rule_score"] + data["statistical_score"] + data["cross_validation_score"]
        ).clip(upper=100.0)
        score_after_semantic_context = np.where(
            data["is_bulk_procurement"],
            np.clip(
                additive_score,
                self.config.bulk_procurement_min_score,
                self.config.bulk_procurement_score_cap,
            ),
            additive_score,
        )
        score_after_asset_whitelist = np.where(
            data["is_asset_whitelist"],
            self.config.asset_whitelist_score,
            score_after_semantic_context,
        )
        score_after_expense_rules = np.where(
            data["is_split_reimbursement"]
            | data["is_same_value_split"]
            | data["is_frequency_anomaly"],
            self.config.same_value_split_score,
            score_after_asset_whitelist,
        )
        score_after_asset_frequency = np.where(
            data["is_asset_frequency_anomaly"],
            self.config.asset_frequency_score,
            score_after_expense_rules,
        )
        score_after_payroll = np.where(
            data["is_payroll_department_regular"],
            self.config.payroll_department_score,
            score_after_asset_frequency,
        )
        data["Risk_Score"] = np.where(
            data["is_payroll_fraud"],
            self.config.payroll_fraud_score,
            score_after_payroll,
        )
        feedback_match, feedback_detail = self._feedback_matches(data)
        data["Risk_Score"] = np.where(
            feedback_match,
            np.maximum(
                data["Risk_Score"] - self.config.feedback_score_reduction,
                0.0,
            ),
            data["Risk_Score"],
        )
        data["is_human_verified_match"] = feedback_match.to_numpy()
        data["human_feedback_detail"] = feedback_detail.to_numpy()
        data["Risk_Level"] = np.select(
            [
                data["is_refund"],
                data["Risk_Score"] > self.config.high_risk_threshold,
                data["Risk_Score"] >= self.config.medium_risk_threshold,
            ],
            ["退款/红字冲销", "高风险", "中风险"],
            default="低风险",
        )
        per_row_detail = statistical_meta["per_row"]
        if isinstance(per_row_detail, pd.Series):
            data["statistical_detail"] = per_row_detail.reindex(data.index, fill_value="")
        else:
            data["statistical_detail"] = ""
            data.loc[active_mask, "statistical_detail"] = str(per_row_detail)
        data["cross_validation_detail"] = self._build_cross_labels(data, cross)
        data["split_reimbursement_detail"] = self._build_split_labels(data, split_counts)
        data["same_value_split_detail"] = self._build_same_value_labels(
            data,
            same_value_counts,
        )
        data["bulk_procurement_advice"] = self._build_bulk_advice(data)
        data["asset_whitelist_advice"] = self._build_asset_advice(data)
        data["frequency_anomaly_detail"] = self._build_frequency_labels(
            data,
            frequency_counts,
        )
        data["asset_frequency_detail"] = self._build_asset_frequency_labels(
            data,
            frequency_counts,
        )
        data["payroll_advice"] = self._build_payroll_advice(data)
        data["Risk_Features"] = data.apply(self._combine_features, axis=1)
        data["ai_advice"] = ""
        data["ai_status"] = np.select(
            [
                data["is_refund"],
                data["Risk_Score"] > self.config.ai_trigger_threshold,
                data["is_asset_whitelist"],
                data["is_bulk_procurement"],
                data["is_payroll"],
            ],
            [
                "无需AI（退款/冲销）",
                "待AI研判",
                "建议人工复核（资产价格）",
                "建议人工复核（大宗采购）",
                "建议人工复核（薪酬）",
            ],
            default="无需AI",
        )

        high_count = int((data["Risk_Score"] > self.config.high_risk_threshold).sum())
        medium_count = int(
            (
                (data["Risk_Score"] >= self.config.medium_risk_threshold)
                & (data["Risk_Score"] <= self.config.high_risk_threshold)
            ).sum()
        )
        metrics = {
            "total_count": len(data),
            "analyzed_count": int(active_mask.sum()),
            "total_amount": float(data.loc[active_mask, "amount"].sum()),
            "refund_count": int(refund_mask.sum()),
            "refund_amount": float(data.loc[refund_mask, "amount"].sum()),
            "high_risk_count": high_count,
            "medium_risk_count": medium_count,
            "anomaly_count": high_count + medium_count,
            "high_risk_amount": float(
                data.loc[
                    data["Risk_Score"] > self.config.high_risk_threshold,
                    "amount",
                ].sum()
            ),
            "average_risk_score": (
                float(data.loc[active_mask, "Risk_Score"].mean()) if active_mask.any() else 0.0
            ),
            "max_risk_score": (
                float(data.loc[active_mask, "Risk_Score"].max()) if active_mask.any() else 0.0
            ),
            "flagged_makers": int(
                data.loc[
                    active_mask & (data["Risk_Score"] > self.config.high_risk_threshold),
                    "user",
                ].nunique()
            ),
        }
        return RiskAssessment(
            data=data,
            metrics=metrics,
            distribution={
                **amount_stats,
                **{key: value for key, value in statistical_meta.items() if key != "per_row"},
            },
            warnings=warnings,
        )

    def score(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Convenience method for notebooks and automated jobs."""

        return self.evaluate(frame).data

    def _amount_distribution(
        self,
        amounts: pd.Series,
        warnings: list[str],
    ) -> dict[str, float | str]:
        raw_values = amounts.to_numpy(dtype=float)
        if raw_values.size == 0:
            return {
                "n": 0.0,
                "trimmed_n": 0.0,
                "mean": 0.0,
                "min": 0.0,
                "max": 0.0,
                "std": 0.0,
                "q1": 0.0,
                "median": 0.0,
                "q3": 0.0,
                "iqr": 0.0,
                "mad": 0.0,
            }
        trim = self.config.statistical_trim_quantile
        if 0 < trim < 0.5:
            ordered = np.sort(raw_values)
            trim_count = int(ordered.size * trim)
            values = ordered[trim_count : ordered.size - trim_count]
        else:
            values = raw_values
        if values.size < 4:
            values = raw_values
        if values.size == 1:
            only = float(values[0])
            return {
                "n": 1.0,
                "trimmed_n": 1.0,
                "mean": only,
                "min": only,
                "max": only,
                "std": 0.0,
                "q1": only,
                "median": only,
                "q3": only,
                "iqr": 0.0,
                "mad": 0.0,
            }
        description = scipy_stats.describe(values)
        q1, median, q3 = np.quantile(values, [0.25, 0.5, 0.75])
        iqr = float(q3 - q1)
        mad = float(np.median(np.abs(values - median)))
        if len(values) < 8:
            warnings.append("样本量较小，统计异常结论仅供参考。")
        return {
            "n": float(raw_values.size),
            "trimmed_n": float(values.size),
            "mean": float(description.mean),
            "min": float(description.minmax[0]),
            "max": float(description.minmax[1]),
            "std": float(np.sqrt(description.variance)) if description.variance >= 0 else 0.0,
            "q1": float(q1),
            "median": float(median),
            "q3": float(q3),
            "iqr": iqr,
            "mad": mad,
        }

    def _holiday_flags(self, dates: pd.Series) -> tuple[pd.Series, str | None]:
        if holidays_lib is None:
            return pd.Series(False, index=dates.index), (
                "未安装 holidays，本次未执行中国法定节假日识别；周末和凌晨规则不受影响。"
            )
        years = sorted({int(year) for year in dates.dt.year.dropna().unique()})
        if not years:
            return pd.Series(False, index=dates.index), None
        try:
            calendar = holidays_lib.country_holidays("CN", years=years)
            unique_dates = dates.dt.date
            flags = unique_dates.map(lambda value: value in calendar).astype(bool)
            return pd.Series(flags, index=dates.index), None
        except Exception as exc:
            return pd.Series(False, index=dates.index), (
                f"中国节假日日历初始化失败，本次跳过节假日规则：{type(exc).__name__}"
            )

    def _near_duplicate_flags(self, frame: pd.DataFrame) -> pd.Series:
        flags = pd.Series(False, index=frame.index, dtype=bool)
        work = frame[["date", "user", "amount"]].copy()
        work["_day"] = work["date"].dt.normalize()
        for _, group in work.groupby(["_day", "user"], sort=False):
            if len(group) < 2:
                continue
            ordered = group.sort_values("amount")
            values = ordered["amount"].to_numpy(dtype=float)
            previous = values[:-1]
            current = values[1:]
            denominator = np.maximum(np.abs(previous), np.finfo(float).eps)
            close = np.abs(current - previous) / denominator < self.config.near_duplicate_pct
            if close.any():
                positions = np.flatnonzero(close)
                labels = ordered.index.to_numpy()
                flags.loc[labels[positions]] = True
                flags.loc[labels[positions + 1]] = True
        return flags

    def _split_reimbursement_flags(
        self,
        frame: pd.DataFrame,
        active_mask: pd.Series,
    ) -> tuple[pd.Series, pd.Series]:
        split = pd.Series(False, index=frame.index, dtype=bool)
        counts = pd.Series(0, index=frame.index, dtype="int64")
        fixed_amount = np.isclose(
            pd.to_numeric(frame["amount"], errors="coerce"),
            4_999.0,
            atol=0.01,
        )
        eligible = active_mask & fixed_amount
        if not eligible.any():
            return split, counts

        working = frame.loc[eligible, ["date", "user"]].copy()
        working["_day"] = working["date"].dt.normalize()
        group_counts = working.groupby(["user", "_day"])["date"].transform("size")
        for index, count in group_counts.items():
            counts.loc[index] = int(count)
            if count >= 3:
                split.loc[index] = True
        return split, counts

    def _same_value_split_flags(
        self,
        frame: pd.DataFrame,
        active_mask: pd.Series,
    ) -> tuple[pd.Series, pd.Series]:
        split = pd.Series(False, index=frame.index, dtype=bool)
        counts = pd.Series(0, index=frame.index, dtype="int64")
        eligible = active_mask & pd.to_numeric(frame["amount"], errors="coerce").gt(0)
        if not eligible.any():
            return split, counts

        working = frame.loc[eligible, ["date", "user", "amount"]].copy()
        working["_day"] = working["date"].dt.normalize()
        working["_amount"] = working["amount"].round(2)
        group_counts = working.groupby(["user", "_day", "_amount"])["date"].transform("size")
        for index, count in group_counts.items():
            counts.loc[index] = int(count)
            if count >= self.config.same_value_split_min_count:
                split.loc[index] = True
        return split, counts

    def _bulk_procurement_flags(
        self,
        frame: pd.DataFrame,
        active_mask: pd.Series,
    ) -> pd.Series:
        summaries = frame["summary"].fillna("").astype(str)
        has_term = pd.Series(False, index=frame.index)
        for term in self.config.bulk_procurement_terms:
            has_term |= summaries.str.contains(re.escape(term), case=False, na=False)
        return (
            active_mask
            & has_term
            & pd.to_numeric(frame["amount"], errors="coerce").ge(
                self.config.bulk_procurement_min_amount
            )
        )

    def _asset_price_whitelist_flags(
        self,
        frame: pd.DataFrame,
        active_mask: pd.Series,
    ) -> tuple[pd.Series, pd.Series]:
        matched = pd.Series(False, index=frame.index, dtype=bool)
        categories = pd.Series("", index=frame.index, dtype="object")
        summaries = frame["summary"].fillna("").astype(str)
        amounts = pd.to_numeric(frame["amount"], errors="coerce")
        for keyword, lower, upper in sorted(
            self.config.asset_price_ranges,
            key=lambda item: len(item[0]),
            reverse=True,
        ):
            candidate = (
                active_mask
                & summaries.str.contains(re.escape(keyword), case=False, na=False)
                & amounts.between(lower, upper, inclusive="both")
                & ~matched
            )
            matched |= candidate
            categories.loc[candidate] = keyword
        return matched, categories

    def _frequency_anomaly_flags(
        self,
        frame: pd.DataFrame,
        active_mask: pd.Series,
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        anomaly = pd.Series(False, index=frame.index, dtype=bool)
        keywords = pd.Series("", index=frame.index, dtype="object")
        counts = pd.Series(0, index=frame.index, dtype="int64")
        summaries = frame["summary"].fillna("").astype(str)

        for term in sorted(self.config.frequency_terms, key=len, reverse=True):
            candidate = summaries.str.contains(re.escape(term), case=False, na=False)
            keywords.loc[active_mask & candidate & keywords.eq("")] = term

        watch = frame.loc[active_mask & keywords.ne("")].copy()
        watch["_frequency_keyword"] = keywords.loc[watch.index]
        for _, group in watch.groupby(["user", "_frequency_keyword"], sort=False):
            ordered = group.sort_values("date")
            dates = ordered["date"].to_numpy()
            indices = ordered.index.to_list()
            left = 0
            window_counts: dict[Any, int] = {}
            for right, current_date in enumerate(dates):
                while current_date - dates[left] > np.timedelta64(
                    self.config.frequency_window_days,
                    "D",
                ):
                    left += 1
                window_size = right - left + 1
                if window_size >= self.config.frequency_min_count:
                    for index in indices[left : right + 1]:
                        anomaly.loc[index] = True
                        window_counts[index] = max(window_counts.get(index, 0), window_size)
            for index, count in window_counts.items():
                counts.loc[index] = count

        return anomaly, keywords, counts

    def _payroll_flags(
        self,
        frame: pd.DataFrame,
        active_mask: pd.Series,
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        summaries = frame["summary"].fillna("").astype(str)
        users = frame["user"].fillna("").astype(str)
        payroll = pd.Series(False, index=frame.index)
        for term in self.config.payroll_terms:
            payroll |= summaries.str.contains(re.escape(term), case=False, na=False)
        payroll &= active_mask

        department = pd.Series(False, index=frame.index)
        for term in self.config.payroll_department_terms:
            department |= users.str.contains(re.escape(term), case=False, na=False)

        amount = pd.to_numeric(frame["amount"], errors="coerce")
        payroll_fraud = (
            payroll & ~department & amount.ge(self.config.payroll_individual_alert_amount)
        )
        department_regular = (
            payroll
            & department
            & amount.between(
                self.config.payroll_department_min_amount,
                self.config.payroll_department_max_amount,
                inclusive="both",
            )
        )
        return payroll, payroll_fraud, department_regular

    def _feedback_matches(
        self,
        frame: pd.DataFrame,
    ) -> tuple[pd.Series, pd.Series]:
        matched = pd.Series(False, index=frame.index, dtype=bool)
        details = pd.Series("", index=frame.index, dtype="object")
        if self.feedback_store is None:
            return matched, details
        for index, row in frame.iterrows():
            entry = self.feedback_store.find_match(
                summary=row.get("summary", ""),
                amount=row.get("amount", 0),
            )
            if entry:
                matched.loc[index] = True
                details.loc[index] = (
                    "人工复核反馈：历史同类摘要且金额相近的凭证已核实无误"
                    f"（风险分自动下调 {self.config.feedback_score_reduction:g} 分）"
                )
        return matched, details

    def _vague_summary_flags(
        self,
        frame: pd.DataFrame,
        amount_stats: dict[str, float | str],
    ) -> pd.Series:
        q90 = float(frame["amount"].quantile(self.config.vague_amount_quantile))
        large_threshold = max(
            float(amount_stats["mean"]) * self.config.extreme_mean_multiplier,
            q90,
        )
        summaries = frame["summary"].fillna("").astype(str).str.strip()
        compact = summaries.str.replace(
            r"[\s,，。.;；:：、/\\_\-—()（）【】\[\]]",
            "",
            regex=True,
        )
        contains_vague = pd.Series(False, index=frame.index)
        for term in self.config.vague_summary_terms:
            contains_vague |= compact.str.contains(re.escape(term), case=False, na=False)
        contains_detail = pd.Series(False, index=frame.index)
        for marker in self.config.detail_markers:
            contains_detail |= summaries.str.contains(re.escape(marker), case=False, na=False)
        return (
            contains_vague
            & ~contains_detail
            & compact.str.len().le(self.config.vague_summary_max_chars)
            & frame["amount"].ge(large_threshold)
        )

    def _statistical_outlier_flags(
        self,
        amounts: pd.Series,
        amount_stats: dict[str, float | str],
        warnings: list[str],
    ) -> tuple[pd.Series, dict[str, Any]]:
        n = len(amounts)
        if n < self.config.minimum_statistical_sample:
            return (
                pd.Series(False, index=amounts.index),
                {
                    "method": "未启用",
                    "method_label": "样本量不足",
                    "lower": np.nan,
                    "upper": np.nan,
                    "per_row": "样本量不足，未启用",
                },
            )

        values = amounts.to_numpy(dtype=float)
        mean = float(amount_stats["mean"])
        standard_deviation = float(amount_stats["std"])
        z_values = (
            (values - mean) / standard_deviation
            if standard_deviation > 0
            else np.zeros_like(values)
        )
        q1 = float(amount_stats["q1"])
        q3 = float(amount_stats["q3"])
        iqr = float(amount_stats["iqr"])
        iqr_upper = q3 + self.config.iqr_multiplier * iqr
        iqr_lower = max(0.0, q1 - self.config.iqr_multiplier * iqr)

        if self.config.statistical_method == "zscore":
            flags = np.abs(z_values) > self.config.zscore_threshold
            method = "zscore"
            method_label = (
                f"截尾 {self.config.statistical_trim_quantile:.0%} 后 "
                f"Z-score 绝对值 > {self.config.zscore_threshold:g}"
            )
            lower = max(
                0.0,
                float(amount_stats["mean"] - self.config.zscore_threshold * amount_stats["std"]),
            )
            upper = float(amount_stats["mean"] + self.config.zscore_threshold * amount_stats["std"])
        elif iqr > 0:
            flags = (values < iqr_lower) | (values > iqr_upper)
            method = "iqr"
            method_label = (
                f"截尾 {self.config.statistical_trim_quantile:.0%} 后 "
                f"IQR × {self.config.iqr_multiplier:g}"
            )
            lower = iqr_lower
            upper = iqr_upper
        else:
            flags = np.abs(z_values) > self.config.zscore_threshold
            method = "zscore_fallback"
            method_label = (
                f"截尾 {self.config.statistical_trim_quantile:.0%} 后 IQR=0，自动回退至 Z-score"
            )
            lower = max(
                0.0,
                float(amount_stats["mean"] - self.config.zscore_threshold * amount_stats["std"]),
            )
            upper = float(amount_stats["mean"] + self.config.zscore_threshold * amount_stats["std"])
            warnings.append("IQR 为 0，统计层自动采用 Z-score。")

        details = []
        for index, value, z_value in zip(amounts.index, values, z_values, strict=True):
            if flags[amounts.index.get_loc(index)]:
                details.append(
                    f"{method_label}；金额 {value:,.2f}，Z={z_value:.2f}，"
                    f"正常参考区间约 [{lower:,.2f}, {upper:,.2f}]"
                )
            else:
                details.append("")
        return pd.Series(flags, index=amounts.index), {
            "method": method,
            "method_label": method_label,
            "lower": lower,
            "upper": upper,
            "per_row": pd.Series(details, index=amounts.index),
        }

    def _cross_validation_flags(
        self,
        frame: pd.DataFrame,
        weekend: pd.Series,
        night: pd.Series,
        amount_stats: dict[str, float | str],
    ) -> pd.Series:
        large_threshold = max(
            self.config.cross_min_amount,
            float(frame["amount"].quantile(0.75)),
        )
        rounded = (frame["amount"] - frame["amount"].round()).abs() <= np.maximum(
            self.config.cross_rounding_floor,
            frame["amount"].abs() * self.config.cross_rounding_pct,
        )
        eligible = weekend & night & rounded & frame["amount"].ge(large_threshold)
        cross = pd.Series(False, index=frame.index, dtype=bool)
        if not eligible.any():
            return cross

        working = frame.loc[eligible, ["user", "date"]].copy()
        working["_day"] = working["date"].dt.normalize()
        counts = working.groupby(["user", "_day"])["date"].transform("size")
        for index, group_count in counts.items():
            if group_count >= self.config.cross_min_vouchers:
                cross.loc[index] = True
        return cross

    def _build_rule_labels(self, data: pd.DataFrame) -> pd.Series:
        labels: list[str] = []
        for _, row in data.iterrows():
            row_labels: list[str] = []
            if row["is_extreme_amount"]:
                row_labels.append(f"单笔金额超过均值 {self.config.extreme_mean_multiplier:g} 倍")
            if row["is_near_duplicate"]:
                row_labels.append(
                    f"同日同人存在差异小于 {self.config.near_duplicate_pct:.0%} 的近似金额"
                )
            if row["is_weekend"]:
                row_labels.append("周末录入")
            if row["is_holiday"]:
                row_labels.append("法定节假日录入")
            if row["is_night"]:
                row_labels.append(
                    f"凌晨 {self.config.night_start_hour:02d}:00-"
                    f"{self.config.night_end_hour:02d}:00 录入"
                )
            if row["is_vague_high_amount"]:
                row_labels.append("模糊摘要且金额巨大、缺少明细")
            labels.append(_join_labels(row_labels))
        return pd.Series(labels, index=data.index, dtype="object")

    @staticmethod
    def _build_cross_labels(data: pd.DataFrame, cross: pd.Series) -> pd.Series:
        labels = []
        for index, is_cross in cross.items():
            if is_cross:
                row = data.loc[index]
                labels.append(f"制单人“{row['user']}”在周末凌晨提交多笔接近整数的大额费用")
            else:
                labels.append("")
        return pd.Series(labels, index=data.index, dtype="object")

    @staticmethod
    def _build_split_labels(data: pd.DataFrame, counts: pd.Series) -> pd.Series:
        labels: list[str] = []
        for index, row in data.iterrows():
            if row["is_split_reimbursement"]:
                labels.append(
                    f"拆分报销（化整为零）：同一制单人当日提交 {int(counts.loc[index])} 笔 "
                    "4,999.00 元报销，专项规则直接计 80 分"
                )
            else:
                labels.append("")
        return pd.Series(labels, index=data.index, dtype="object")

    @staticmethod
    def _build_same_value_labels(data: pd.DataFrame, counts: pd.Series) -> pd.Series:
        labels: list[str] = []
        for index, row in data.iterrows():
            if row["is_same_value_split"]:
                labels.append(
                    "同一天内多笔等额报销，疑似拆分逃避审批"
                    f"（{int(counts.loc[index])} 笔等额，专项计 80 分）"
                )
            else:
                labels.append("")
        return pd.Series(labels, index=data.index, dtype="object")

    @staticmethod
    def _build_frequency_labels(data: pd.DataFrame, counts: pd.Series) -> pd.Series:
        labels: list[str] = []
        for index, row in data.iterrows():
            if row["is_frequency_anomaly"] and not row["is_asset_frequency_anomaly"]:
                labels.append(
                    "短期内高频采购同类物资，疑似拆分采购或虚报需求"
                    f"（{row['frequency_keyword']}，30 天内 {int(counts.loc[index])} 次）"
                )
            else:
                labels.append("")
        return pd.Series(labels, index=data.index, dtype="object")

    @staticmethod
    def _build_asset_frequency_labels(data: pd.DataFrame, counts: pd.Series) -> pd.Series:
        labels: list[str] = []
        for index, row in data.iterrows():
            if row["is_asset_frequency_anomaly"]:
                labels.append(
                    "短期内高频采购同类大额资产，高度疑似虚构采购套取资金"
                    f"（{row['frequency_keyword']}，30 天内 {int(counts.loc[index])} 次）"
                )
            else:
                labels.append("")
        return pd.Series(labels, index=data.index, dtype="object")

    @staticmethod
    def _build_bulk_advice(data: pd.DataFrame) -> pd.Series:
        advice = "大额业务支出，建议核实对应的采购合同、入库单及发票明细"
        return pd.Series(
            np.where(
                data["is_bulk_procurement"] & ~data["is_asset_whitelist"],
                advice,
                "",
            ),
            index=data.index,
            dtype="object",
        )

    @staticmethod
    def _build_asset_advice(data: pd.DataFrame) -> pd.Series:
        advice = "大宗资产采购，建议核验入库单"
        return pd.Series(
            np.where(data["is_asset_whitelist"], advice, ""),
            index=data.index,
            dtype="object",
        )

    @staticmethod
    def _build_payroll_advice(data: pd.DataFrame) -> pd.Series:
        values: list[str] = []
        for _, row in data.iterrows():
            if row["is_payroll_fraud"]:
                values.append("个人提交大额薪酬，疑似虚构员工套现")
            elif row["is_payroll_department_regular"]:
                values.append("部门统一制单且金额处于常规薪酬区间，风险已下调")
            elif row["is_payroll"]:
                values.append("薪酬凭证需核对工资表、人员名单及银行代发记录")
            else:
                values.append("")
        return pd.Series(values, index=data.index, dtype="object")

    @staticmethod
    def _combine_features(row: pd.Series) -> str:
        if row["is_refund"]:
            return "退款/红字冲销；不参与常规风险评分"
        labels: list[str] = []
        if row["rule_flags"]:
            labels.extend(str(row["rule_flags"]).split("；"))
        if row["is_statistical_outlier"] and row["statistical_detail"]:
            labels.append(str(row["statistical_detail"]))
        if row["is_cross_validated"] and row["cross_validation_detail"]:
            labels.append(str(row["cross_validation_detail"]))
        if row["is_split_reimbursement"] and row["split_reimbursement_detail"]:
            labels.append(str(row["split_reimbursement_detail"]))
        if row["is_same_value_split"] and row["same_value_split_detail"]:
            labels.append(str(row["same_value_split_detail"]))
        if row["is_bulk_procurement"] and row["bulk_procurement_advice"]:
            labels.append(str(row["bulk_procurement_advice"]))
        if row["is_asset_whitelist"] and row["asset_whitelist_advice"]:
            labels.append(str(row["asset_whitelist_advice"]))
        if row["is_frequency_anomaly"] and row["frequency_anomaly_detail"]:
            labels.append(str(row["frequency_anomaly_detail"]))
        if row["is_asset_frequency_anomaly"] and row["asset_frequency_detail"]:
            labels.append(str(row["asset_frequency_detail"]))
        if row["is_payroll"] and row["payroll_advice"]:
            labels.append(str(row["payroll_advice"]))
        if row["is_human_verified_match"] and row["human_feedback_detail"]:
            labels.append(str(row["human_feedback_detail"]))
        return _join_labels(labels)
