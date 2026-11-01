"""Single source of truth for account classification and financial statements."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import Any

import pandas as pd

REVENUE_ACCOUNTS = ("主营业务收入", "其他业务收入", "营业外收入", "其他收益")
DEFAULT_STATUSES: frozenset[str] = frozenset({"generated"})
INCOME_CATEGORIES = frozenset(
    {
        "revenue",
        "cost",
        "selling_expense",
        "admin_expense",
        "finance_expense",
        "other_expense",
        "income_tax_expense",
        "asset_disposal_gain",
    }
)
ASSET_CATEGORIES = frozenset(
    {
        "cash",
        "receivables",
        "inventory",
        "fixed_assets",
        "contra_assets",
        "other_assets",
    }
)


def classify_account(account: str) -> str | None:
    """Map a Chinese account name to one canonical statement category."""

    normalized = str(account or "").strip()
    if not normalized:
        return None
    if "固定资产清理" in normalized:
        return "other_assets"
    if "资产处置损益" in normalized:
        return "asset_disposal_gain"
    if "累计折旧" in normalized:
        return "contra_assets"
    if any(term in normalized for term in ("银行存款", "库存现金", "货币资金")):
        return "cash"
    if any(term in normalized for term in ("应收账款", "应收票据", "应收款项")):
        return "receivables"
    if any(
        term in normalized for term in ("原材料", "库存商品", "生产成本", "制造费用", "周转材料")
    ):
        return "inventory"
    if any(
        term in normalized
        for term in ("固定资产", "在建工程", "无形资产", "投资性房地产", "生产设备")
    ):
        return "fixed_assets"
    if "应付职工薪酬" in normalized or "应付工资" in normalized:
        return "payroll"
    if "应交税费" in normalized or "应缴税费" in normalized:
        return "taxes"
    if any(term in normalized for term in ("短期借款", "长期借款", "合同负债", "预收账款")):
        return "other_liabilities"
    if any(term in normalized for term in ("应付账款", "应付票据")):
        return "payables"
    if "未分配利润" in normalized or "本年利润" in normalized:
        return "retained_earnings"
    if any(term in normalized for term in ("实收资本", "资本公积", "盈余公积")):
        return "contributed_capital"
    if "应付" in normalized:
        return "other_liabilities"
    if any(term in normalized for term in ("应收", "预付", "待摊", "长期股权投资")):
        return "other_assets"
    if any(term in normalized for term in REVENUE_ACCOUNTS):
        return "revenue"
    if any(term in normalized for term in ("主营业务成本", "其他业务成本")):
        return "cost"
    if "销售费用" in normalized:
        return "selling_expense"
    if any(term in normalized for term in ("管理费用", "研发费用")):
        return "admin_expense"
    if "财务费用" in normalized:
        return "finance_expense"
    if "所得税费用" in normalized:
        return "income_tax_expense"
    if any(
        term in normalized for term in ("税金及附加", "营业外支出", "资产减值损失", "信用减值损失")
    ):
        return "other_expense"
    return None


def trial_balance(
    entries: Iterable[dict[str, Any]],
    *,
    statuses: Collection[str] | None = DEFAULT_STATUSES,
) -> pd.DataFrame:
    """Aggregate debit and credit activity by account with Pandas groupby."""

    rows: list[dict[str, Any]] = []
    for entry in entries:
        if statuses is not None and entry.get("status") not in statuses:
            continue
        try:
            amount = round(abs(float(entry.get("amount", 0) or 0)), 2)
        except (TypeError, ValueError):
            continue
        if amount == 0:
            continue
        debit_account = str(entry.get("debit", "")).strip()
        credit_account = str(entry.get("credit", "")).strip()
        if debit_account:
            rows.append(
                {
                    "科目": debit_account,
                    "借方发生额": amount,
                    "贷方发生额": 0.0,
                }
            )
        if credit_account:
            rows.append(
                {
                    "科目": credit_account,
                    "借方发生额": 0.0,
                    "贷方发生额": amount,
                }
            )
    columns = ["科目", "借方发生额", "贷方发生额"]
    if not rows:
        return pd.DataFrame(columns=columns)
    return (
        pd.DataFrame(rows)
        .groupby("科目", as_index=False)
        .agg({"借方发生额": "sum", "贷方发生额": "sum"})
        .sort_values("科目")
        .round(2)
        .reset_index(drop=True)
    )


def statement_figures(
    entries: Iterable[dict[str, Any]],
    *,
    statuses: Collection[str] | None = DEFAULT_STATUSES,
) -> tuple[dict[str, float], dict[str, float]]:
    """Calculate canonical balance-sheet and profit figures."""

    balance = {
        "cash": 0.0,
        "receivables": 0.0,
        "inventory": 0.0,
        "fixed_assets": 0.0,
        "contra_assets": 0.0,
        "other_assets": 0.0,
        "payables": 0.0,
        "payroll": 0.0,
        "taxes": 0.0,
        "other_liabilities": 0.0,
        "contributed_capital": 0.0,
        "retained_earnings": 0.0,
    }
    profit = {
        "revenue": 0.0,
        "cost": 0.0,
        "selling_expense": 0.0,
        "admin_expense": 0.0,
        "finance_expense": 0.0,
        "other_expense": 0.0,
        "income_tax_expense": 0.0,
        "asset_disposal_gain": 0.0,
    }

    for _, row in trial_balance(entries, statuses=statuses).iterrows():
        account = str(row["科目"]).strip()
        debit_amount = float(row["借方发生额"])
        credit_amount = float(row["贷方发生额"])
        category = classify_account(account)
        if category is None:
            balance["other_assets"] += debit_amount
            balance["other_liabilities"] += credit_amount
            continue
        if category in INCOME_CATEGORIES:
            profit[category] += (
                credit_amount - debit_amount
                if category in {"revenue", "asset_disposal_gain"}
                else debit_amount - credit_amount
            )
            continue
        if category == "contra_assets":
            balance[category] += credit_amount - debit_amount
        elif category in ASSET_CATEGORIES:
            balance[category] += debit_amount - credit_amount
        else:
            balance[category] += credit_amount - debit_amount

    for key in balance:
        balance[key] = round(balance[key], 2)
    for key in profit:
        profit[key] = round(profit[key], 2)
    profit["operating_profit"] = round(
        profit["revenue"]
        - profit["cost"]
        - profit["selling_expense"]
        - profit["admin_expense"]
        - profit["finance_expense"]
        - profit["other_expense"],
        2,
    )
    profit["net_profit"] = round(
        profit["operating_profit"] + profit["asset_disposal_gain"] - profit["income_tax_expense"],
        2,
    )
    return balance, profit


def balance_sheet_values(
    entries: Iterable[dict[str, Any]],
    *,
    statuses: Collection[str] | None = DEFAULT_STATUSES,
) -> dict[str, float]:
    balance, profit = statement_figures(entries, statuses=statuses)
    fixed_asset_value = round(balance["fixed_assets"] - balance["contra_assets"], 2)
    retained_earnings = round(balance["retained_earnings"] + profit["net_profit"], 2)
    total_assets = round(
        balance["cash"]
        + balance["receivables"]
        + balance["inventory"]
        + fixed_asset_value
        + balance["other_assets"],
        2,
    )
    total_liabilities_equity = round(
        balance["payables"]
        + balance["payroll"]
        + balance["taxes"]
        + balance["other_liabilities"]
        + balance["contributed_capital"]
        + retained_earnings,
        2,
    )
    return {
        "cash": balance["cash"],
        "receivables": balance["receivables"],
        "inventory": balance["inventory"],
        "fixed_assets": fixed_asset_value,
        "other_assets": balance["other_assets"],
        "payables": balance["payables"],
        "payroll": balance["payroll"],
        "taxes": balance["taxes"],
        "other_liabilities": balance["other_liabilities"],
        "contributed_capital": balance["contributed_capital"],
        "retained_earnings": retained_earnings,
        "total_assets": total_assets,
        "total_liabilities_equity": total_liabilities_equity,
        "balance_check": round(total_assets - total_liabilities_equity, 2),
    }


def income_statement_values(
    entries: Iterable[dict[str, Any]],
    *,
    statuses: Collection[str] | None = DEFAULT_STATUSES,
) -> dict[str, float]:
    _, profit = statement_figures(entries, statuses=statuses)
    return profit


def balance_sheet_rows(
    entries: Iterable[dict[str, Any]],
    *,
    statuses: Collection[str] | None = DEFAULT_STATUSES,
) -> list[tuple[str, float | None]]:
    values = balance_sheet_values(entries, statuses=statuses)
    return [
        ("资产类", None),
        ("货币资金", values["cash"]),
        ("应收账款", values["receivables"]),
        ("存货", values["inventory"]),
        ("固定资产", values["fixed_assets"]),
        ("其他资产", values["other_assets"]),
        ("资产总计", values["total_assets"]),
        ("负债及所有者权益类", None),
        ("应付账款", values["payables"]),
        ("应付职工薪酬", values["payroll"]),
        ("应交税费", values["taxes"]),
        ("其他负债", values["other_liabilities"]),
        ("实收资本", values["contributed_capital"]),
        ("未分配利润", values["retained_earnings"]),
        ("负债及所有者权益总计", values["total_liabilities_equity"]),
        ("平衡校验", values["balance_check"]),
    ]


def income_statement_rows(
    entries: Iterable[dict[str, Any]],
    *,
    statuses: Collection[str] | None = DEFAULT_STATUSES,
) -> list[tuple[str, float]]:
    profit = income_statement_values(entries, statuses=statuses)
    return [
        ("一、营业收入", profit["revenue"]),
        ("减：营业成本", profit["cost"]),
        ("    销售费用", profit["selling_expense"]),
        ("    管理费用", profit["admin_expense"]),
        ("    财务费用", profit["finance_expense"]),
        ("    其他经营费用", profit["other_expense"]),
        ("二、营业利润", profit["operating_profit"]),
        ("加：资产处置损益", profit["asset_disposal_gain"]),
        ("减：所得税费用", profit["income_tax_expense"]),
        ("三、净利润", profit["net_profit"]),
    ]
