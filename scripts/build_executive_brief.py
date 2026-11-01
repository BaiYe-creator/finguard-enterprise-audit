"""Build FinGuard's five-page executive deck from a cleaned data file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main(argv: list[str] | None = None) -> int:
    from finguard.data_ingestion import DataIngestionService
    from finguard.ppt_generator import generate_executive_ppt
    from finguard.risk_engine import RiskEngine

    parser = argparse.ArgumentParser(description="生成 FinGuard 高管汇报 PPT")
    parser.add_argument(
        "--data",
        default=str(PROJECT_ROOT / "Real_Data_Clean.csv"),
        help="清洗后数据路径",
    )
    parser.add_argument(
        "--template",
        default=str(PROJECT_ROOT / "assets" / "template.pptx"),
        help="PPT 模版路径",
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "FinGuard_高管简报_极简版.pptx"),
        help="输出 PPT 路径",
    )
    args = parser.parse_args(argv)

    ingestion = DataIngestionService().load_path(args.data)
    assessment = RiskEngine().evaluate(ingestion.clean_data)
    result = generate_executive_ppt(
        assessment.data,
        assessment.metrics,
        template_path=args.template,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result.content)
    print(f"output={output.resolve()}")
    print(f"slides={result.slide_count}")
    print(f"vouchers={assessment.metrics['total_count']}")
    print(f"high_risk={assessment.metrics['high_risk_count']}")
    print(f"used_ai={result.used_ai}")
    print(f"ai_status={result.fallback_reason or 'OK'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
