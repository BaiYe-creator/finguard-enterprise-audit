"""Deterministic synthetic data for product evaluation and automated tests."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd


def build_demo_dataframe(row_count: int = 82) -> pd.DataFrame:
    """Return messy, realistic demonstration journals.

    All names, identifiers, and amounts are synthetic. The dataset intentionally
    includes weekend, holiday, midnight, duplicate-value, and statistical
    anomalies so every risk layer can be demonstrated without client data.
    """

    rng = np.random.RandomState(20261027)
    people = ["陈晨", "李敏", "王涛", "赵宁", "孙悦", "周敏"]
    categories = [
        "服务器配件采购｜发票号 INV-2026-0812｜数量 8",
        "客户差旅交通费｜行程上海-杭州｜项目编号 P-108",
        "研发耗材采购｜订单 PO-260919｜供应商星河科技",
        "员工培训场地费｜合同 HT-2026-019｜明细见附件",
        "仓库维修费｜出库单 CK-0831｜使用部门行政部",
        "软件订阅费｜订阅号 SUB-7781｜项目 FinGuard",
        "快递物流费｜运单号 SF-9281831｜部门销售部",
        "客户会议茶歇｜发票号 FP-83412｜参会人员 12 人",
    ]

    rows: list[dict[str, object]] = []
    current = date(2026, 8, 3)
    while len(rows) < max(20, row_count - 4):
        if current.weekday() < 5:
            amount = float(rng.lognormal(mean=6.55, sigma=0.48))
            amount = round(min(max(amount, 120), 4_500), 2)
            hour = int(rng.randint(8, 20))
            minute = int(rng.randint(0, 60))
            rows.append(
                {
                    "凭证编号": f"PZ-{current:%Y%m%d}-{len(rows) + 1:04d}",
                    "日期": current.strftime("%Y-%m-%d"),
                    "时间": f"{hour:02d}:{minute:02d}",
                    "经办人": people[len(rows) % len(people)],
                    "说明": categories[len(rows) % len(categories)],
                    "借": f"¥{amount:,.2f}" if len(rows) % 3 == 0 else amount,
                    "贷": 0,
                }
            )
        current += timedelta(days=1)

    # A mid-risk weekend-entry control sample.
    rows.append(
        {
            "凭证编号": "PZ-20260919-9001",
            "日期": "2026/09/19",
            "时间": "14:36",
            "经办人": "王涛",
            "说明": "周六值班交通费",
            "借": 860,
            "贷": 0,
        }
    )
    # A statistical-only outlier below the 3x mean extreme rule.
    rows.append(
        {
            "凭证编号": "PZ-20260923-9002",
            "日期": "2026-09-23",
            "时间": "10:18",
            "经办人": "李敏",
            "说明": "临时会议场地服务｜合同 HT-2026-027｜会议签到表附后",
            "借": "¥3,850.00",
            "贷": 0,
        }
    )
    # Two synthetic high-risk entries that activate all three evidence layers.
    rows.extend(
        [
            {
                "凭证编号": "PZ-20261003-9901",
                "日期": "2026-10-03",
                "时间": "02:16",
                "经办人": "周敏",
                "说明": "办公用品一批",
                "借": "¥128,000.00",
                "贷": 0,
            },
            {
                "凭证编号": "PZ-20261003-9902",
                "日期": "2026-10-03",
                "时间": "03:04",
                "经办人": "周敏",
                "说明": "劳保一批",
                "借": "¥128,600.00",
                "贷": 0,
            },
        ]
    )
    rows.append(
        {
            "凭证编号": "合计",
            "日期": "",
            "时间": "",
            "经办人": "",
            "说明": "本期合计",
            "借": "¥258,310.00",
            "贷": "¥0.00",
        }
    )
    return pd.DataFrame(rows)


def demo_csv_bytes() -> bytes:
    return build_demo_dataframe().to_csv(index=False).encode("utf-8-sig")
