from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from finguard.feedback_store import FeedbackStore
from finguard.risk_engine import RiskEngine


def test_feedback_matches_similar_summary_and_amount_and_reduces_risk() -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        store = FeedbackStore(Path(directory) / "feedback.json")
        store.add(
            voucher_id="a_46",
            summary="采购割草机｜商品成本",
            amount=3175.60,
            risk_features="大宗资产采购，建议核验入库单",
            user="Max",
        )
        assert store.count() == 1

        data = pd.DataFrame(
            {
                "voucher_id": ["a_99"],
                "date": pd.to_datetime(["2023-05-01"]),
                "user": ["Max"],
                "summary": ["采购割草机｜商品成本"],
                "debit": [3_200.0],
                "credit": [0.0],
                "amount": [3_200.0],
                "is_refund": [False],
                "_has_explicit_time": [False],
            }
        )
        without_feedback = RiskEngine().evaluate(data).data.iloc[0]
        with_feedback = RiskEngine(feedback_store=store).evaluate(data).data.iloc[0]
        assert without_feedback["Risk_Score"] == 45
        assert with_feedback["Risk_Score"] == 25
        assert with_feedback["is_human_verified_match"]
        assert "人工复核反馈" in with_feedback["Risk_Features"]
