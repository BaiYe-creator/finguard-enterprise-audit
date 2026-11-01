"""Board-ready Excel report generation."""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd


class ReportGenerationError(RuntimeError):
    """Raised when an Excel report cannot be generated."""


REPORT_COLUMNS: dict[str, str] = {
    "voucher_id": "凭证号",
    "date": "日期时间",
    "user": "制单人",
    "summary": "摘要",
    "debit": "借方金额",
    "credit": "贷方金额",
    "amount": "凭证金额",
    "Risk_Score": "风险分",
    "Risk_Level": "风险等级",
    "Risk_Features": "触发风险特征",
    "ai_advice": "AI审计研判",
    "ai_status": "AI研判状态",
    "transaction_type": "业务类型",
}

FLAG_COLUMNS: dict[str, str] = {
    "is_extreme_amount": "金额极值",
    "is_near_duplicate": "近似金额",
    "is_weekend": "周末",
    "is_holiday": "节假日",
    "is_night": "凌晨",
    "is_vague_high_amount": "模糊大额",
    "is_statistical_outlier": "统计离群",
    "is_cross_validated": "交叉验证",
}


def _safe_excel_text(value: Any, limit: int = 32_000) -> Any:
    if not isinstance(value, str):
        return value
    text = value[:limit]
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def _naive_datetime(value: Any) -> datetime | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    if getattr(parsed, "tzinfo", None) is not None:
        parsed = parsed.tz_localize(None)
    return parsed.to_pydatetime()


def _yes_no(value: Any) -> str:
    return "是" if bool(value) else "否"


def build_excel_report(
    scored_data: pd.DataFrame,
    metrics: dict[str, Any],
    ingestion_report: dict[str, Any] | None = None,
    distribution: dict[str, Any] | None = None,
    rejected_data: pd.DataFrame | None = None,
    generated_at: datetime | None = None,
) -> bytes:
    """Build an auditable multi-sheet XLSX report in memory.

    The workbook includes management summary, exception detail with AI advice,
    quality evidence, and the full cleaned population for traceability.
    """

    try:
        import xlsxwriter
    except ImportError as exc:
        raise ReportGenerationError("未安装 xlsxwriter，无法生成 Excel 报告") from exc

    if scored_data.empty:
        raise ReportGenerationError("没有可导出的凭证")
    if len(scored_data) > 1_048_575:
        raise ReportGenerationError("导出数据超过 Excel 单工作表行数上限")

    generated = generated_at or datetime.now()
    ingestion = ingestion_report or {}
    distribution = distribution or {}
    anomalies = scored_data.loc[
        pd.to_numeric(scored_data["Risk_Score"], errors="coerce") >= 30
    ].copy()
    anomalies = anomalies.sort_values(
        ["Risk_Score", "amount"],
        ascending=[False, False],
    )

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(
        output,
        {
            "in_memory": True,
            "nan_inf_to_errors": True,
            "strings_to_formulas": False,
            "strings_to_urls": False,
        },
    )
    workbook.set_properties(
        {
            "title": "FinGuard 企业财务异常合规审计报告",
            "subject": "财务凭证异常检测与 AI 辅助研判",
            "author": "FinGuard",
            "company": "FinGuard Open Source",
            "comments": "本报告为审计线索，不构成违法事实认定。",
        }
    )

    colors = {
        "navy": "#102A43",
        "blue": "#1F5A7A",
        "teal": "#167D7F",
        "red": "#8B1E2D",
        "red_light": "#FBE9EB",
        "yellow": "#FFF2CC",
        "yellow_dark": "#5C4300",
        "gray": "#627D98",
        "border": "#D9E2EC",
        "white": "#FFFFFF",
        "green": "#E3FCEF",
    }

    fmt = {
        "title": workbook.add_format(
            {
                "bold": True,
                "font_size": 22,
                "font_color": colors["white"],
                "bg_color": colors["navy"],
                "font_name": "Microsoft YaHei",
                "align": "left",
                "valign": "vcenter",
                "indent": 1,
            }
        ),
        "subtitle": workbook.add_format(
            {
                "font_size": 10,
                "font_color": colors["white"],
                "bg_color": colors["navy"],
                "font_name": "Microsoft YaHei",
                "align": "left",
                "valign": "vcenter",
                "indent": 1,
            }
        ),
        "section": workbook.add_format(
            {
                "bold": True,
                "font_size": 13,
                "font_color": colors["navy"],
                "bottom": 1,
                "bottom_color": colors["teal"],
                "font_name": "Microsoft YaHei",
            }
        ),
        "label": workbook.add_format(
            {
                "bold": True,
                "font_name": "Microsoft YaHei",
                "font_color": colors["gray"],
                "bg_color": "#F0F4F8",
                "border": 1,
                "border_color": colors["border"],
                "valign": "vcenter",
            }
        ),
        "value": workbook.add_format(
            {
                "font_name": "Microsoft YaHei",
                "border": 1,
                "border_color": colors["border"],
                "valign": "vcenter",
            }
        ),
        "money": workbook.add_format(
            {
                "font_name": "Microsoft YaHei",
                "num_format": "#,##0.00;[Red]-#,##0.00",
                "border": 1,
                "border_color": colors["border"],
                "align": "right",
            }
        ),
        "integer": workbook.add_format(
            {
                "font_name": "Microsoft YaHei",
                "num_format": "0",
                "border": 1,
                "border_color": colors["border"],
                "align": "center",
            }
        ),
        "header": workbook.add_format(
            {
                "bold": True,
                "font_color": colors["white"],
                "bg_color": colors["blue"],
                "font_name": "Microsoft YaHei",
                "border": 1,
                "border_color": colors["blue"],
                "align": "center",
                "valign": "vcenter",
                "text_wrap": True,
            }
        ),
        "text": workbook.add_format(
            {
                "font_name": "Microsoft YaHei",
                "border": 1,
                "border_color": colors["border"],
                "valign": "top",
                "text_wrap": True,
            }
        ),
        "date": workbook.add_format(
            {
                "font_name": "Microsoft YaHei",
                "num_format": "yyyy-mm-dd hh:mm:ss",
                "border": 1,
                "border_color": colors["border"],
                "valign": "top",
                "align": "left",
            }
        ),
        "center": workbook.add_format(
            {
                "font_name": "Microsoft YaHei",
                "border": 1,
                "border_color": colors["border"],
                "valign": "top",
                "align": "center",
            }
        ),
        "high": workbook.add_format(
            {
                "font_name": "Microsoft YaHei",
                "font_color": colors["white"],
                "bg_color": colors["red"],
                "border": 1,
                "border_color": "#6E1521",
                "valign": "top",
                "text_wrap": True,
            }
        ),
        "medium": workbook.add_format(
            {
                "font_name": "Microsoft YaHei",
                "font_color": colors["yellow_dark"],
                "bg_color": colors["yellow"],
                "border": 1,
                "border_color": "#E6C85C",
                "valign": "top",
                "text_wrap": True,
            }
        ),
        "high_money": workbook.add_format(
            {
                "font_name": "Microsoft YaHei",
                "font_color": colors["white"],
                "bg_color": colors["red"],
                "border": 1,
                "border_color": "#6E1521",
                "num_format": "#,##0.00",
                "valign": "top",
                "align": "right",
            }
        ),
        "medium_money": workbook.add_format(
            {
                "font_name": "Microsoft YaHei",
                "font_color": colors["yellow_dark"],
                "bg_color": colors["yellow"],
                "border": 1,
                "border_color": "#E6C85C",
                "num_format": "#,##0.00",
                "valign": "top",
                "align": "right",
            }
        ),
        "high_date": workbook.add_format(
            {
                "font_name": "Microsoft YaHei",
                "font_color": colors["white"],
                "bg_color": colors["red"],
                "border": 1,
                "border_color": "#6E1521",
                "num_format": "yyyy-mm-dd hh:mm:ss",
                "valign": "top",
                "align": "left",
            }
        ),
        "medium_date": workbook.add_format(
            {
                "font_name": "Microsoft YaHei",
                "font_color": colors["yellow_dark"],
                "bg_color": colors["yellow"],
                "border": 1,
                "border_color": "#E6C85C",
                "num_format": "yyyy-mm-dd hh:mm:ss",
                "valign": "top",
                "align": "left",
            }
        ),
        "note": workbook.add_format(
            {
                "font_name": "Microsoft YaHei",
                "font_size": 9,
                "font_color": colors["gray"],
                "text_wrap": True,
                "valign": "top",
            }
        ),
    }

    summary_ws = workbook.add_worksheet("管理摘要")
    detail_ws = workbook.add_worksheet("异常明细")
    full_ws = workbook.add_worksheet("全部凭证")
    quality_ws = workbook.add_worksheet("数据质量")
    chart_ws = workbook.add_worksheet("图表数据")

    for sheet in (summary_ws, detail_ws, full_ws, quality_ws, chart_ws):
        sheet.hide_gridlines(2)
    summary_ws.set_tab_color(colors["teal"])
    detail_ws.set_tab_color(colors["red"])
    full_ws.set_tab_color(colors["blue"])
    quality_ws.set_tab_color(colors["yellow_dark"])
    chart_ws.hide()

    summary_ws.hide_gridlines(2)
    summary_ws.merge_range("A1:F2", "FinGuard 企业财务异常合规审计报告", fmt["title"])
    summary_ws.merge_range(
        "A3:F3",
        "风险矩阵 + 统计检验 + AI 辅助研判｜本报告用于发现审计线索，不构成事实或法律结论",
        fmt["subtitle"],
    )
    summary_ws.set_row(0, 32)
    summary_ws.set_row(1, 26)
    summary_ws.set_column("A:A", 19)
    summary_ws.set_column("B:F", 17)

    summary_ws.write("A5", "审计概览", fmt["section"])
    cards = [
        ("凭证总笔数", f"{int(metrics.get('total_count', len(scored_data))):,}", "笔"),
        ("凭证总金额", float(metrics.get("total_amount", scored_data["amount"].sum())), "元"),
        ("高风险笔数", f"{int(metrics.get('high_risk_count', 0)):,}", "笔"),
        ("高风险金额", float(metrics.get("high_risk_amount", 0)), "元"),
    ]
    card_positions = [(6, 0), (6, 3), (8, 0), (8, 3)]
    for (row, col), (label, value, unit) in zip(card_positions, cards, strict=True):
        summary_ws.merge_range(row, col, row, col + 2, label, fmt["label"])
        if isinstance(value, float):
            summary_ws.write_number(row + 1, col, value, fmt["money"])
            summary_ws.write(row + 1, col + 1, unit, fmt["value"])
            summary_ws.merge_range(row + 1, col + 1, row + 1, col + 2, unit, fmt["value"])
        else:
            summary_ws.write(row + 1, col, value, fmt["value"])
            summary_ws.merge_range(row + 1, col + 1, row + 1, col + 2, unit, fmt["value"])

    summary_ws.write("A11", "风险分布", fmt["section"])
    distribution_rows = [
        ("高风险（>60）", int(metrics.get("high_risk_count", 0))),
        ("中风险（30-60）", int(metrics.get("medium_risk_count", 0))),
        ("低风险（<30）", int(len(scored_data) - metrics.get("anomaly_count", 0))),
    ]
    summary_ws.write_row(11, 0, ["等级", "凭证笔数", "占比"], fmt["header"])
    for row_index, (label, count) in enumerate(distribution_rows, start=12):
        summary_ws.write(row_index, 0, label, fmt["value"])
        summary_ws.write_number(row_index, 1, count, fmt["integer"])
        summary_ws.write_number(
            row_index,
            2,
            count / max(len(scored_data), 1),
            workbook.add_format(
                {"num_format": "0.0%", "border": 1, "border_color": colors["border"]}
            ),
        )

    summary_ws.write("A17", "审计口径与模型信息", fmt["section"])
    methodology = [
        ("硬规则层", "单层 +30；金额极值、同日近似金额、敏感时间、模糊大额摘要"),
        (
            "统计异常层",
            f"单层 +40；{distribution.get('method_label', 'IQR')}，样本门槛 8 笔",
        ),
        ("交叉验证层", "单层 +30；周末凌晨、多名近似整数大额凭证的群体行为"),
        ("AI 触发条件", "Risk_Score 严格大于 60；如未配置密钥则保留为人工复核项"),
        ("风险总分", "三层可叠加，封顶 100 分；30-60 为中风险，>60 为高风险"),
    ]
    for row_index, (label, value) in enumerate(methodology, start=18):
        summary_ws.write(row_index, 0, label, fmt["label"])
        summary_ws.merge_range(row_index, 1, row_index, 5, value, fmt["value"])

    summary_ws.merge_range(
        "A24:F26",
        "审计提示：量化评分是风险筛选工具，不能替代凭证检查、访谈、函证、实物盘点或司法鉴定。"
        "AI 输出可能存在遗漏、误判和表述偏差，必须由具备权限的审计人员复核并保留工作底稿。"
        "涉及个人信息的数据由使用者依法确定处理目的、授权基础和保留期限。",
        fmt["note"],
    )
    summary_ws.set_row(24, 32)
    summary_ws.set_row(25, 24)
    summary_ws.set_landscape()
    summary_ws.set_paper(9)
    summary_ws.fit_to_pages(1, 1)
    summary_ws.set_zoom(90)

    # Exception detail with full-row risk colours.
    detail_ws.merge_range(
        "A1:M1",
        f"异常明细（共 {len(anomalies):,} 笔）｜深红：高风险 >60；黄色：中风险 30-60",
        fmt["title"],
    )
    detail_ws.set_row(0, 32)
    headers = [*REPORT_COLUMNS.values(), *FLAG_COLUMNS.values()]
    detail_ws.write_row(2, 0, headers, fmt["header"])
    detail_ws.set_row(2, 38)

    for row_index, (_, record) in enumerate(anomalies.iterrows(), start=3):
        score = float(record.get("Risk_Score", 0) or 0)
        if score > 60:
            text_format = fmt["high"]
            money_format = fmt["high_money"]
            date_format = fmt["high_date"]
        elif score >= 30:
            text_format = fmt["medium"]
            money_format = fmt["medium_money"]
            date_format = fmt["medium_date"]
        else:
            text_format = fmt["text"]
            money_format = fmt["money"]
            date_format = fmt["date"]

        for col_index, key in enumerate(REPORT_COLUMNS):
            value = record.get(key, "")
            if key == "date":
                written = _naive_datetime(value)
                if written is None:
                    detail_ws.write_string(row_index, col_index, "", text_format)
                else:
                    detail_ws.write_datetime(row_index, col_index, written, date_format)
            elif key in {"debit", "credit", "amount"}:
                number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
                if pd.isna(number):
                    detail_ws.write_string(row_index, col_index, "", text_format)
                else:
                    detail_ws.write_number(row_index, col_index, float(number), money_format)
            elif key == "Risk_Score":
                detail_ws.write_number(row_index, col_index, score, text_format)
            elif key == "ai_advice" and not str(value).strip():
                detail_ws.write_string(
                    row_index,
                    col_index,
                    "未生成。请在系统配置 AI 服务后重跑，或由审计人员人工研判。",
                    text_format,
                )
            elif isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(
                value, bool
            ):
                detail_ws.write_number(row_index, col_index, float(value), text_format)
            else:
                detail_ws.write_string(
                    row_index,
                    col_index,
                    str(_safe_excel_text(value)),
                    text_format,
                )

        for offset, key in enumerate(FLAG_COLUMNS, start=len(REPORT_COLUMNS)):
            detail_ws.write_string(row_index, offset, _yes_no(record.get(key, False)), text_format)

    detail_widths = [18, 20, 13, 32, 15, 15, 15, 11, 11, 52, 72, 26, 18]
    for index, width in enumerate(detail_widths):
        detail_ws.set_column(index, index, width)
    for offset in range(len(REPORT_COLUMNS), len(REPORT_COLUMNS) + len(FLAG_COLUMNS)):
        detail_ws.set_column(offset, offset, 12)
    detail_ws.freeze_panes(3, 3)
    detail_ws.autofilter(2, 0, max(2, len(anomalies) + 2), len(headers) - 1)
    if not anomalies.empty:
        detail_ws.set_row(
            3, 72, fmt["high"] if anomalies.iloc[0]["Risk_Score"] > 60 else fmt["medium"]
        )
    detail_ws.set_landscape()
    detail_ws.fit_to_pages(1, 0)
    detail_ws.repeat_rows(2)
    detail_ws.set_zoom(85)

    # Full cleaned population for traceability.
    full_headers = [*REPORT_COLUMNS.values()]
    full_ws.write_row(0, 0, full_headers, fmt["header"])
    full_ws.set_row(0, 32)
    for row_index, (_, record) in enumerate(scored_data.sort_values("date").iterrows(), start=1):
        for col_index, key in enumerate(REPORT_COLUMNS):
            value = record.get(key, "")
            if key == "date":
                date_value = _naive_datetime(value)
                if date_value is None:
                    full_ws.write_blank(row_index, col_index, None, fmt["text"])
                else:
                    full_ws.write_datetime(row_index, col_index, date_value, fmt["date"])
            elif key in {"debit", "credit", "amount", "Risk_Score"}:
                number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
                if pd.isna(number):
                    full_ws.write_blank(row_index, col_index, None, fmt["text"])
                else:
                    full_ws.write_number(
                        row_index,
                        col_index,
                        float(number),
                        fmt["money"] if key != "Risk_Score" else fmt["integer"],
                    )
            else:
                full_ws.write_string(
                    row_index, col_index, str(_safe_excel_text(value)), fmt["text"]
                )
    full_ws.set_column("A:A", 18)
    full_ws.set_column("B:B", 20)
    full_ws.set_column("C:C", 13)
    full_ws.set_column("D:D", 32)
    full_ws.set_column("E:G", 15)
    full_ws.set_column("H:I", 11)
    full_ws.set_column("J:J", 54)
    full_ws.set_column("K:K", 72)
    full_ws.set_column("L:L", 22)
    full_ws.set_column("M:M", 18)
    full_ws.freeze_panes(1, 3)
    full_ws.autofilter(0, 0, max(0, len(scored_data)), len(full_headers) - 1)
    full_ws.repeat_rows(0)
    full_ws.set_landscape()

    # Quality evidence.
    quality_ws.merge_range("A1:D1", "数据接入与清洗质量报告", fmt["title"])
    quality_ws.set_row(0, 32)
    quality_ws.write_row(2, 0, ["指标", "结果", "说明", "审计影响"], fmt["header"])
    quality_rows = [
        ("源文件", ingestion.get("source_file", "未提供"), "", "用于底稿追溯"),
        ("SHA-256", ingestion.get("source_sha256", "未提供"), "", "验证原始文件完整性"),
        ("原始行数", ingestion.get("raw_rows", len(scored_data)), "", "导入规模"),
        ("有效凭证", ingestion.get("clean_rows", len(scored_data)), "", "进入风险矩阵"),
        ("空行清理", ingestion.get("blank_rows_removed", 0), "", "排除无业务内容行"),
        ("汇总行清理", ingestion.get("summary_rows_removed", 0), "", "排除合计/余额行"),
        ("无效日期隔离", ingestion.get("invalid_dates_removed", 0), "", "需回到源系统核对"),
        ("无效金额隔离", ingestion.get("invalid_amounts_removed", 0), "", "需回到源系统核对"),
        ("质量结论", ingestion.get("quality", "通过"), "", "决定后续样本可信度"),
        ("生成时间", generated.strftime("%Y-%m-%d %H:%M:%S"), "", "工作底稿时间戳"),
    ]
    for row_index, row in enumerate(quality_rows, start=3):
        for col_index, value in enumerate(row):
            quality_ws.write_string(row_index, col_index, str(_safe_excel_text(value)), fmt["text"])
    quality_ws.set_column("A:A", 22)
    quality_ws.set_column("B:B", 55)
    quality_ws.set_column("C:C", 2)
    quality_ws.set_column("D:D", 28)
    quality_ws.freeze_panes(3, 0)

    if rejected_data is not None and not rejected_data.empty:
        rejected_ws = workbook.add_worksheet("隔离记录")
        rejected_ws.set_tab_color(colors["gray"])
        rejected_ws.write_row(
            0,
            0,
            ["原始行", "凭证号", "日期", "制单人", "摘要", "借方", "贷方", "隔离原因"],
            fmt["header"],
        )
        for row_index, (_, record) in enumerate(rejected_data.iterrows(), start=1):
            values = [
                record.get("_source_row", ""),
                record.get("voucher_id", ""),
                record.get("date", ""),
                record.get("user", ""),
                record.get("summary", ""),
                record.get("debit", ""),
                record.get("credit", ""),
                record.get("_reject_reason", ""),
            ]
            for col_index, value in enumerate(values):
                rejected_ws.write_string(
                    row_index,
                    col_index,
                    str(_safe_excel_text(value)),
                    fmt["text"],
                )
        rejected_ws.freeze_panes(1, 0)
        rejected_ws.autofilter(0, 0, len(rejected_data), 7)
        rejected_ws.set_column("A:A", 10)
        rejected_ws.set_column("B:E", 20)
        rejected_ws.set_column("D:G", 18)
        rejected_ws.set_column("H:H", 28)

    # Chart data and native Excel charts.
    chart_ws.write_row(0, 0, ["制单人", "异常金额", "异常笔数"], fmt["header"])
    anomalies_by_user = (
        anomalies.groupby("user", dropna=False)
        .agg(amount=("amount", "sum"), count=("voucher_id", "count"))
        .sort_values("amount", ascending=False)
        .head(12)
    )
    for row_index, (user, values) in enumerate(anomalies_by_user.iterrows(), start=1):
        chart_ws.write_string(row_index, 0, str(user), fmt["text"])
        chart_ws.write_number(row_index, 1, float(values["amount"]), fmt["money"])
        chart_ws.write_number(row_index, 2, int(values["count"]), fmt["integer"])
    chart_ws.set_column("A:A", 20)
    chart_ws.set_column("B:C", 16)

    if not anomalies_by_user.empty:
        pie = workbook.add_chart({"type": "pie"})
        pie.add_series(
            {
                "name": "异常金额占比",
                "categories": ["图表数据", 1, 0, len(anomalies_by_user), 0],
                "values": ["图表数据", 1, 1, len(anomalies_by_user), 1],
                "data_labels": {"percentage": True, "font": {"size": 8}},
                "points": [
                    {"fill": {"color": color}}
                    for color in (
                        "#8B1E2D",
                        "#C05A63",
                        "#D9A441",
                        "#167D7F",
                        "#1F5A7A",
                        "#829AB1",
                    )
                ],
            }
        )
        pie.set_title({"name": "异常金额按制单人分布"})
        pie.set_legend({"position": "bottom", "font": {"size": 8}})
        pie.set_size({"width": 520, "height": 330})
        summary_ws.insert_chart("D11", pie, {"x_offset": 10, "y_offset": 10})

    trend_chart_row = len(anomalies_by_user) + 3
    chart_ws.write(trend_chart_row - 1, 0, "日期", fmt["header"])
    chart_ws.write(trend_chart_row - 1, 1, "异常金额", fmt["header"])
    trend = (
        anomalies.assign(day=pd.to_datetime(anomalies["date"]).dt.date)
        .groupby("day", as_index=False)["amount"]
        .sum()
        .sort_values("day")
    )
    for offset, record in enumerate(trend.itertuples(index=False), start=trend_chart_row):
        chart_ws.write_string(offset, 0, str(record.day), fmt["text"])
        chart_ws.write_number(offset, 1, float(record.amount), fmt["money"])
    if not trend.empty:
        line = workbook.add_chart({"type": "line"})
        line.add_series(
            {
                "name": "异常金额",
                "categories": ["图表数据", trend_chart_row, 0, trend_chart_row + len(trend) - 1, 0],
                "values": ["图表数据", trend_chart_row, 1, trend_chart_row + len(trend) - 1, 1],
                "line": {"color": colors["red"], "width": 2.25},
                "marker": {"type": "circle", "size": 5, "fill": {"color": colors["red"]}},
            }
        )
        line.set_title({"name": "异常发生日期趋势"})
        line.set_y_axis(
            {
                "num_format": "#,##0",
                "major_gridlines": {"visible": True, "line": {"color": "#E6EEF5"}},
            }
        )
        line.set_legend({"none": True})
        line.set_size({"width": 760, "height": 330})
        summary_ws.insert_chart("A30", line, {"x_offset": 5, "y_offset": 10})

    try:
        workbook.close()
    except Exception as exc:
        raise ReportGenerationError(f"Excel 报告写入失败：{exc}") from exc
    return output.getvalue()


def report_filename(prefix: str = "FinGuard_审计报告", when: datetime | None = None) -> str:
    moment = when or datetime.now()
    return f"{prefix}_{moment:%Y%m%d_%H%M}.xlsx"
