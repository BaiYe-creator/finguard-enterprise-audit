"""Clean SAP journal-entry exports into one row per posting."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

REQUIRED_COLUMNS = {
    "posting_id",
    "document_date",
    "user",
    "text",
    "gl_account",
    "gl_account_name",
    "amount",
    "cd_flag",
}
OUTPUT_COLUMNS = ["voucher_id", "date", "user", "summary", "amount"]

ITEM_TRANSLATIONS = {
    "Garden Hose": "花园水管",
    "Garden Shears": "园艺剪",
    "Gardening Gloves": "园艺手套",
    "Hedge Trimmer": "绿篱修剪机",
    "Lawn Mower": "割草机",
    "Leaf Blower": "吹叶机",
    "Rake": "耙子",
    "Shovel": "铁锹",
    "Watering Can": "洒水壶",
    "Wheelbarrow": "手推车",
}
ACCOUNT_TRANSLATIONS = {
    "3400": "商品成本",
    "1576": "可抵扣进项税",
    "1600": "应付账款",
}


class JournalCleanError(ValueError):
    """Raised when the SAP source schema or amount mode is invalid."""


def read_journal(path: str | Path) -> pd.DataFrame:
    source = Path(path)
    if not source.exists():
        raise JournalCleanError(f"文件不存在：{source}")

    if source.suffix.casefold() in {".xlsx", ".xlsm"}:
        frame = pd.read_excel(source, dtype=object)
    elif source.suffix.casefold() == ".csv":
        frame = _read_csv(source)
    else:
        raise JournalCleanError("仅支持 CSV、XLSX 或 XLSM")

    frame.columns = [str(column).strip() for column in frame.columns]
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise JournalCleanError("SAP 数据缺少字段：" + "、".join(missing))
    return frame


def _read_csv(path: Path) -> pd.DataFrame:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return pd.read_csv(path, encoding=encoding, dtype=object)
        except (UnicodeDecodeError, pd.errors.ParserError) as exc:
            last_error = exc
    raise JournalCleanError(f"CSV 无法读取：{last_error}")


def parse_amount(value: Any) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip().replace(",", "").replace("，", "")
    text = re.sub(r"[^0-9.\-+]", "", text)
    if not text or text in {"-", "+", ".", "-.", "+."}:
        return 0.0
    negative = "-" in text
    text = text.replace("-", "").replace("+", "")
    if text.count(".") > 1:
        first, *rest = text.split(".")
        text = first + "." + "".join(rest)
    try:
        amount = float(text)
    except ValueError:
        return 0.0
    return -amount if negative else amount


def translate_summary(text: Any, gl_account: Any, account_name: Any) -> str:
    raw_text = str(text).strip() if not pd.isna(text) else "未注明商品"
    translated_item = ITEM_TRANSLATIONS.get(raw_text, raw_text)
    account_key = str(gl_account).strip()
    translated_account = ACCOUNT_TRANSLATIONS.get(account_key)
    if translated_account is None:
        translated_account = (
            str(account_name).strip() if not pd.isna(account_name) else "未识别科目"
        )
    return f"采购{translated_item}｜{translated_account}"


def clean_sap_journal(
    frame: pd.DataFrame,
    *,
    amount_mode: str = "cost_account",
    cost_account: str = "3400",
) -> pd.DataFrame:
    """Return exactly five normalized columns and one row per posting.

    ``cost_account`` uses only the D/3400 merchandise-cost line.
    ``debit_total`` sums all D lines, including deductible input tax.
    C (credit/payable) lines are always removed before either calculation.
    """

    if amount_mode not in {"cost_account", "debit_total"}:
        raise JournalCleanError("amount_mode 仅支持 cost_account 或 debit_total")

    work = frame[list(REQUIRED_COLUMNS)].copy()
    work["amount"] = work["amount"].map(parse_amount)
    work["posting_id"] = work["posting_id"].astype(str).str.strip()
    work["document_date"] = pd.to_datetime(work["document_date"], errors="coerce")
    work["user"] = work["user"].fillna("未知制单人").astype(str).str.strip()
    work["cd_flag"] = work["cd_flag"].astype(str).str.strip().str.upper()
    work["gl_account"] = work["gl_account"].astype(str).str.strip()

    debit_rows = work.loc[work["cd_flag"].eq("D")].copy()
    if debit_rows.empty:
        raise JournalCleanError("过滤贷方 C 后没有借方凭证")

    cost_rows = debit_rows.loc[debit_rows["gl_account"].eq(str(cost_account).strip())].copy()
    if cost_rows.empty:
        raise JournalCleanError(f"没有找到 cd_flag == D 且科目为 {cost_account} 的成本行")

    cost_rows["summary"] = [
        translate_summary(row.text, row.gl_account, row.gl_account_name)
        for row in cost_rows.itertuples(index=False)
    ]
    cost_base = (
        cost_rows.groupby("posting_id", as_index=False)
        .agg(
            date=("document_date", "min"),
            user=("user", "first"),
            summary=("summary", "first"),
        )
        .rename(columns={"posting_id": "voucher_id"})
    )

    if amount_mode == "cost_account":
        amount = (
            cost_rows.groupby("posting_id", as_index=False)["amount"]
            .sum()
            .rename(columns={"posting_id": "voucher_id", "amount": "amount"})
        )
    else:
        amount = (
            debit_rows.groupby("posting_id", as_index=False)["amount"]
            .sum()
            .rename(columns={"posting_id": "voucher_id", "amount": "amount"})
        )

    result = cost_base.merge(amount, on="voucher_id", how="left")
    result = result.loc[result["date"].notna()].copy()
    result["amount"] = result["amount"].round(2)
    result["date"] = result["date"].dt.strftime("%Y-%m-%d")
    result = result[OUTPUT_COLUMNS].sort_values(["date", "voucher_id"]).reset_index(drop=True)
    return result


def write_clean_csv(frame: pd.DataFrame, output_path: str | Path) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(target, index=False, encoding="utf-8-sig")
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将 SAP 多行日记账清洗为每个 posting_id 一行",
    )
    parser.add_argument("source", help="SAP 原始 CSV/XLSX 路径")
    parser.add_argument(
        "-o",
        "--output",
        default="Real_Data_Clean.csv",
        help="输出 CSV 路径，默认 Real_Data_Clean.csv",
    )
    parser.add_argument(
        "--amount-mode",
        choices=("cost_account", "debit_total"),
        default="cost_account",
        help="cost_account=只取 D/3400 成本；debit_total=汇总全部 D 行",
    )
    parser.add_argument(
        "--cost-account",
        default="3400",
        help="商品成本科目编码，默认 3400",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        source = read_journal(args.source)
        cleaned = clean_sap_journal(
            source,
            amount_mode=args.amount_mode,
            cost_account=args.cost_account,
        )
        output = write_clean_csv(cleaned, args.output)
    except JournalCleanError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"source_rows={len(source)}")
    print(f"vouchers={len(cleaned)}")
    print(f"amount_mode={args.amount_mode}")
    print(f"output={output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
