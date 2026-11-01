"""Human-in-the-loop entry editing and local Pandas report recalculation."""

from __future__ import annotations

import hashlib
import io
import math
import uuid
from typing import Any

import pandas as pd

from finguard.accounting_core import balance_sheet_rows, income_statement_rows
from finguard.accounting_core import trial_balance as core_trial_balance

DISPLAY_COLUMNS = (
    "entry_id",
    "业务描述",
    "借方科目",
    "贷方科目",
    "金额",
    "说明",
    "状态",
)


def source_entries(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create stable editable rows from V0.3 generated journal groups."""

    entries: list[dict[str, Any]] = []
    for index, record in enumerate(history, start=1):
        if record.get("status") != "generated":
            continue
        created_at = str(record.get("created_at", ""))
        line = record.get("line", index)
        order = record.get("entry_order", 1)
        seed = (
            f"{created_at}|{record.get('entry_group', line)}|{line}|"
            f"{order}|{record.get('business', '')}|{index}"
        )
        digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
        entry_id = f"BZ-{int(line):04d}-{int(order):02d}-{digest}"
        entries.append(
            {
                "entry_id": entry_id,
                "business": str(record.get("business", "")),
                "debit": str(record.get("debit", "")),
                "credit": str(record.get("credit", "")),
                "amount": record.get("amount", 0),
                "description": str(record.get("description", "")),
                "status": "generated",
                "created_at": created_at,
                "entry_group": record.get("entry_group", line),
                "entry_order": order,
            }
        )
    return entries


def merge_source_entries(
    source: list[dict[str, Any]],
    saved: list[dict[str, Any]],
    deleted_ids: list[str] | set[str] | None = None,
) -> list[dict[str, Any]]:
    """Merge newly generated source rows without losing saved edits."""

    deleted = set(deleted_ids or [])
    saved_by_id = {str(entry["entry_id"]): entry for entry in saved}
    source_ids = {str(entry["entry_id"]) for entry in source}
    merged: list[dict[str, Any]] = []

    for entry in source:
        entry_id = str(entry["entry_id"])
        if entry_id in deleted:
            continue
        merged.append(saved_by_id.get(entry_id, entry))
    for entry in saved:
        entry_id = str(entry["entry_id"])
        if entry_id in source_ids or entry_id in deleted:
            continue
        merged.append(entry)
    return merged


def editable_frame(entries: list[dict[str, Any]]) -> pd.DataFrame:
    rows = [
        {
            "entry_id": entry["entry_id"],
            "业务描述": entry.get("business", ""),
            "借方科目": entry.get("debit", ""),
            "贷方科目": entry.get("credit", ""),
            "金额": entry.get("amount", 0),
            "说明": entry.get("description", ""),
            "状态": entry.get("status", "generated"),
        }
        for entry in entries
    ]
    return pd.DataFrame(rows, columns=DISPLAY_COLUMNS)


def sanitize_editor_dataframe(
    edited: pd.DataFrame,
) -> tuple[list[dict[str, Any]], int]:
    """Normalize editor values and return rows plus invalid amount count."""

    dataframe = edited.copy()
    for column in DISPLAY_COLUMNS:
        if column not in dataframe:
            dataframe[column] = "" if column != "金额" else 0.0

    for column in ("entry_id", "业务描述", "借方科目", "贷方科目", "说明", "状态"):
        dataframe[column] = dataframe[column].fillna("").astype(str).str.strip()

    meaningful = (
        dataframe["业务描述"].ne("")
        | dataframe["借方科目"].ne("")
        | dataframe["贷方科目"].ne("")
        | dataframe["金额"].notna()
    )
    dataframe = dataframe.loc[meaningful].copy()

    original_amount = dataframe["金额"]
    cleaned_amount = (
        original_amount.astype(str).str.replace(r"[,\s¥￥元人民币]", "", regex=True).str.strip()
    )
    numeric_amount = pd.to_numeric(cleaned_amount, errors="coerce")
    invalid_amount_count = int(
        (
            original_amount.notna()
            & original_amount.astype(str).str.strip().ne("")
            & numeric_amount.isna()
        ).sum()
    )
    dataframe["金额"] = numeric_amount.fillna(0.0).abs().round(2)

    generated_ids: set[str] = set()
    output: list[dict[str, Any]] = []
    for _, row in dataframe.iterrows():
        entry_id = str(row["entry_id"]).strip()
        if not entry_id:
            entry_id = f"V04-NEW-{uuid.uuid4().hex[:12]}"
        if entry_id in generated_ids:
            raise ValueError(f"entry_id 重复：{entry_id}")
        generated_ids.add(entry_id)
        if not row["借方科目"] and not row["贷方科目"] and float(row["金额"]) == 0:
            continue
        output.append(
            {
                "entry_id": entry_id,
                "business": row["业务描述"],
                "debit": row["借方科目"],
                "credit": row["贷方科目"],
                "amount": float(row["金额"]),
                "description": row["说明"],
                "status": row["状态"] or "generated",
            }
        )
    return output, invalid_amount_count


def balance_totals(entries: list[dict[str, Any]]) -> tuple[float, float, bool]:
    debit_total = round(
        sum(
            float(entry.get("amount", 0) or 0)
            for entry in entries
            if str(entry.get("debit", "")).strip()
        ),
        2,
    )
    credit_total = round(
        sum(
            float(entry.get("amount", 0) or 0)
            for entry in entries
            if str(entry.get("credit", "")).strip()
        ),
        2,
    )
    return (
        debit_total,
        credit_total,
        math.isclose(
            debit_total,
            credit_total,
            abs_tol=0.005,
        ),
    )


def trial_balance(entries: list[dict[str, Any]]) -> pd.DataFrame:
    return core_trial_balance(entries, statuses=None)


def balance_sheet(entries: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        balance_sheet_rows(entries, statuses=None),
        columns=["项目", "金额"],
    )


def income_statement(entries: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        income_statement_rows(entries, statuses=None),
        columns=["项目", "金额"],
    )


def recalculate_reports(entries: list[dict[str, Any]]) -> dict[str, pd.DataFrame]:
    return {
        "trial_balance": trial_balance(entries),
        "balance_sheet": balance_sheet(entries),
        "income_statement": income_statement(entries),
    }


def report_xlsx(report: pd.DataFrame, title: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = title
    worksheet.sheet_view.showGridLines = False
    column_count = len(report.columns)
    last_column = get_column_letter(column_count)

    worksheet.merge_cells(f"A1:{last_column}1")
    title_cell = worksheet["A1"]
    title_cell.value = title
    title_cell.font = Font(name="微软雅黑", size=18, bold=True, color="12161B")
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    worksheet.row_dimensions[1].height = 34

    for column_index, column_name in enumerate(report.columns, start=1):
        cell = worksheet.cell(3, column_index, str(column_name))
        cell.font = Font(name="微软雅黑", size=10.5, bold=True, color="12161B")
        cell.fill = PatternFill("solid", fgColor="F2F1EF")
        cell.alignment = Alignment(
            horizontal="left" if column_index == 1 else "right",
            vertical="center",
        )

    thin = Side(style="thin", color="D9D9D9")
    for row_index, row in enumerate(report.itertuples(index=False), start=4):
        for column_index, value in enumerate(row, start=1):
            cell = worksheet.cell(row_index, column_index, value)
            cell.font = Font(name="微软雅黑", size=10, color="20242A")
            cell.border = Border(top=thin if row_index == 4 else None, bottom=thin)
            cell.alignment = Alignment(
                horizontal="left" if column_index == 1 else "right",
                vertical="center",
            )
            if column_index > 1 and isinstance(value, (int, float)):
                cell.number_format = "#,##0.00"

    worksheet.column_dimensions["A"].width = 42
    for index in range(2, column_count + 1):
        worksheet.column_dimensions[get_column_letter(index)].width = 20
    workbook.save(output := io.BytesIO())
    return output.getvalue()
