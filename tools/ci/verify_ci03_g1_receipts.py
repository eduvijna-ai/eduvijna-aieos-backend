#!/usr/bin/env python3
"""Verify CI03-G1 PG18 JSONL receipts against the governed manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.domains.content.api.ci03_g1_receipt_manifest import (  # noqa: E402
    summarize_verification,
    verify_receipt_file,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, help="Path to baseline JSONL")
    parser.add_argument("--candidate", required=True, help="Path to candidate JSONL")
    parser.add_argument("--baseline-sha", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--summary-out", required=True, help="Sanitized summary JSON path")
    args = parser.parse_args()

    baseline_errors, baseline_records = verify_receipt_file(
        args.baseline, mode="baseline", source_sha=args.baseline_sha
    )
    candidate_errors, candidate_records = verify_receipt_file(
        args.candidate, mode="candidate", source_sha=args.candidate_sha
    )

    summary = {
        "baseline": summarize_verification(
            mode="baseline",
            source_sha=args.baseline_sha,
            records=baseline_records,
            errors=baseline_errors,
        ),
        "candidate": summarize_verification(
            mode="candidate",
            source_sha=args.candidate_sha,
            records=candidate_records,
            errors=candidate_errors,
        ),
        "ok": not baseline_errors and not candidate_errors,
    }

    out_path = Path(args.summary_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if not summary["ok"]:
        for block in (summary["baseline"], summary["candidate"]):
            if block["errors"]:
                print(f"CI03_G1_{block['mode'].upper()}_VERIFY=FAIL", file=sys.stderr)
                for err in block["errors"]:
                    print(f"  {err}", file=sys.stderr)
        return 1

    print("CI03_G1_RECEIPT_VERIFY=OK")
    print(f"baseline_cases={summary['baseline']['receipt_count']}")
    print(f"candidate_cases={summary['candidate']['receipt_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())