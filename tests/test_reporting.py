from __future__ import annotations

import io

from openpyxl import load_workbook

from finguard.config import RiskConfig
from finguard.data_ingestion import DataIngestionService
from finguard.demo import demo_csv_bytes
from finguard.reporting import build_excel_report
from finguard.risk_engine import RiskEngine


def test_excel_report_contains_required_sheets_and_high_risk_now() -> None:
    ingestion = DataIngestionService().load_and_clean(
        demo_csv_bytes(),
        filename="demo.csv",
    )
    assessment = RiskEngine(RiskConfig()).evaluate(ingestion.clean_data)
    payload = build_excel_report(
        assessment.data,
        assessment.metrics,
        ingestion.report,
        assessment.distribution,
        ingestion.rejected_data,
    )
    assert payload.startswith(b"PK")
    workbook = load_workbook(io.BytesIO(payload), read_only=True)
    assert {"管理摘要", "异常明细", "全部凭证", "数据质量"}.issubset(workbook.sheetnames)
    assert "AI审计研判" in [
        cell.value for cell in next(workbook["异常明细"].iter_rows(min_row=3, max_row=3))
    ]
    workbook.close()
