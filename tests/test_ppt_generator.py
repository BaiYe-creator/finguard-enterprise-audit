from __future__ import annotations

import io
from types import SimpleNamespace

import pandas as pd
from pptx import Presentation
from pptx.dml.color import RGBColor

from finguard.ppt_generator import AI_FALLBACK, generate_executive_ppt, ppt_report_filename


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "voucher_id",
            "date",
            "user",
            "summary",
            "amount",
            "Risk_Score",
            "Risk_Level",
            "Risk_Features",
            "ai_advice",
        ]
    )


def _load(content: bytes) -> Presentation:
    assert content.startswith(b"PK")
    return Presentation(io.BytesIO(content))


def _all_text(prs: Presentation) -> str:
    parts: list[str] = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text:
                parts.append(shape.text)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    for cell in row.cells:
                        if cell.text:
                            parts.append(cell.text)
    return "\n".join(parts)


def test_empty_dataset_generates_text_fallback_deck() -> None:
    metrics = {
        "total_count": 0,
        "total_amount": 0,
        "high_risk_count": 0,
        "high_risk_amount": 0,
        "flagged_makers": 0,
    }
    result = generate_executive_ppt(_empty_frame(), metrics)
    presentation = _load(result.content)
    assert result.slide_count == 5
    assert "无可绘制的制单人异常金额" in _all_text(presentation)
    assert AI_FALLBACK in _all_text(presentation)
    assert not result.used_ai
    assert result.fallback_reason


def test_large_amounts_generate_pie_chart() -> None:
    data = pd.DataFrame(
        {
            "voucher_id": ["V1", "V2", "V3"],
            "date": pd.to_datetime(["2026-10-01", "2026-10-01", "2026-10-02"]),
            "user": ["甲", "甲", "乙"],
            "summary": ["大额采购｜发票 A", "大额采购｜发票 B", "服务费｜合同 C"],
            "amount": [128_000_000.0, 99_999_999.99, 12_345_678.9],
            "Risk_Score": [100.0, 90.0, 70.0],
            "Risk_Level": ["高风险", "高风险", "高风险"],
            "Risk_Features": ["金额极值", "金额极值", "统计离群"],
            "ai_advice": ["", "", ""],
        }
    )
    metrics = {
        "total_count": 3,
        "total_amount": data["amount"].sum(),
        "high_risk_count": 3,
        "high_risk_amount": data["amount"].sum(),
        "flagged_makers": 2,
    }
    result = generate_executive_ppt(data, metrics)
    presentation = _load(result.content)
    chart_count = sum(
        1
        for slide in presentation.slides
        for shape in slide.shapes
        if getattr(shape, "has_chart", False)
    )
    assert chart_count == 2
    assert "¥128,000,000.00" in _all_text(presentation)
    assert "FinGuard\n企业财务异常审计简报" in _all_text(presentation)
    assert presentation.slides[1].background.fill.fore_color.rgb == RGBColor(
        0xF7,
        0xF5,
        0xF0,
    )

    fonts = {
        run.font.name
        for slide in presentation.slides
        for shape in slide.shapes
        if getattr(shape, "has_text_frame", False)
        for paragraph in shape.text_frame.paragraphs
        for run in paragraph.runs
        if run.font.name
    }
    assert fonts <= {"Noto Serif SC Light", "Noto Sans SC Light"}

    pie = next(
        shape.chart
        for slide in presentation.slides
        for shape in slide.shapes
        if getattr(shape, "has_chart", False) and "PIE" in str(shape.chart.chart_type)
    )
    colors = [str(point.format.fill.fore_color.rgb) for point in pie.plots[0].series[0].points]
    assert colors[:2] == ["12161B", "B08C5A"]


def test_long_chinese_summary_is_truncated_and_wrapped() -> None:
    long_text = "办公用品及劳保用品采购" * 80
    data = pd.DataFrame(
        {
            "voucher_id": ["LONG-1"],
            "date": pd.to_datetime(["2026-10-03 03:04:00"]),
            "user": ["超长姓名用户"],
            "summary": [long_text],
            "amount": [128_600.0],
            "Risk_Score": [100.0],
            "Risk_Level": ["高风险"],
            "Risk_Features": ["凌晨录入；摘要模糊"],
            "ai_advice": ["建议核验发票、合同、出库单和门禁记录。"],
        }
    )
    metrics = {
        "total_count": 1,
        "total_amount": 128_600.0,
        "high_risk_count": 1,
        "high_risk_amount": 128_600.0,
        "flagged_makers": 1,
    }
    result = generate_executive_ppt(data, metrics)
    presentation = _load(result.content)
    text = _all_text(presentation)
    assert "…" in text
    assert "¥128,600.00" in text
    assert not any(
        getattr(shape, "has_table", False)
        for slide in presentation.slides
        for shape in slide.shapes
    )
    assert "FinGuard" in _all_text(presentation)


def test_ppt_filename_is_date_stamped() -> None:
    assert ppt_report_filename().startswith("FinGuard高管简报_极简版_")
    assert ppt_report_filename().endswith(".pptx")


def test_deepseek_is_called_when_key_is_available(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                "上述凭证存在虚构采购与资金套取风险，应立即冻结付款并核验合同、"
                                "入库单、发票和经办人权限，约谈相关人员并保全审批日志。"
                            )
                        )
                    )
                ]
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    data = pd.DataFrame(
        {
            "voucher_id": ["V1"],
            "date": pd.to_datetime(["2026-10-01"]),
            "user": ["张三"],
            "summary": ["采购服务器"],
            "amount": [9000.0],
            "Risk_Score": [90.0],
            "Risk_Level": ["高风险"],
            "Risk_Features": ["高频资产采购"],
            "ai_advice": [""],
        }
    )
    metrics = {
        "total_count": 1,
        "high_risk_count": 1,
        "high_risk_amount": 9000.0,
        "average_risk_score": 90.0,
    }
    result = generate_executive_ppt(data, metrics, api_key="test-key")
    assert result.used_ai
    assert "虚构采购" in result.ai_opinion
    assert calls[0]["timeout"] == 15.0
    assert "约 100 字" in calls[0]["messages"][0]["content"]


def test_business_history_appends_lightweight_business_slide() -> None:
    result = generate_executive_ppt(
        _empty_frame(),
        {
            "total_count": 0,
            "high_risk_count": 0,
            "high_risk_amount": 0,
            "average_risk_score": 0,
        },
        business_history=[
            {
                "business": "采购钢材10吨，金额100000元",
                "debit": "原材料",
                "credit": "银行存款",
                "amount": 100000.0,
                "description": "采购制造用钢材",
                "summary": "本期业务以生产材料采购为主。",
                "status": "generated",
            }
        ],
    )
    presentation = _load(result.content)
    assert result.slide_count == 6
    assert "业财转换明细" in _all_text(presentation)
    assert "本期业务以生产材料采购为主" in _all_text(presentation)
    assert len(result.content) < 2 * 1024 * 1024
