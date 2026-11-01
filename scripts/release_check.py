"""Command-line V1.0 release gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from finguard.release_readiness import run_release_checks, summary  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="FinGuard release readiness checks")
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    checks = run_release_checks(args.root)
    result = {
        "checks": [check.to_dict() for check in checks],
        "summary": summary(checks),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for check in checks:
            print(f"[{check.status}] {check.name}: {check.detail}")
        print(
            f"\nPASS={result['summary']['PASS']} "
            f"WARN={result['summary']['WARN']} "
            f"FAIL={result['summary']['FAIL']}"
        )
    return 1 if result["summary"]["blocking"] else 0


if __name__ == "__main__":
    sys.exit(main())
