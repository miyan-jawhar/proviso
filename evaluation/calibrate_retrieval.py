"""Measure local RAG threshold trade-offs without invoking an LLM.

This is a behavioral calibration over the synthetic fixture, not a claim that a
Chroma score is a production probability of correctness.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


EVALUATION_DIR = Path(__file__).parent
PROJECT_ROOT = EVALUATION_DIR.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
DEFAULT_CASES = EVALUATION_DIR / "cases.json"
DEFAULT_THRESHOLDS = (0.25, 0.30, 0.35, 0.40, 0.45, 0.50)

# Allow `python evaluation/calibrate_retrieval.py` from the repository root,
# matching the command documented in evaluation/README.md.
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _parse_thresholds(raw: str) -> list[float]:
    values = sorted({float(value.strip()) for value in raw.split(",") if value.strip()})
    if not values or any(value < 0 or value > 1 for value in values):
        raise ValueError("Thresholds must be comma-separated values between 0 and 1.")
    return values


def _load_cases(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _is_unknown_vendor_case(case: dict) -> bool:
    return case.get("id", "").startswith("adversarial-unknown-vendor")


def _score_cases(cases: list[dict]) -> list[dict]:
    # Imports stay here so --dry-run does not initialise the local embedding
    # model or Chroma collection.
    from app.agents.graph import _build_vectorstore
    from app.agents.legal_agent import (
        _build_retrieval_plan,
        _matched_vendor_name,
        _unmatched_vendor_reference,
    )

    vectorstore = _build_vectorstore()
    probes: list[dict] = []
    for case in cases:
        expected_vendors = case.get("expected_source_vendors", [])
        if not expected_vendors and not _is_unknown_vendor_case(case):
            continue

        retrieval_query, search_kwargs = _build_retrieval_plan(vectorstore, case["query"])
        results = vectorstore.similarity_search_with_relevance_scores(
            retrieval_query, **search_kwargs
        )
        top_document, top_score = results[0] if results else (None, None)
        top_vendor = top_document.metadata.get("vendor_name") if top_document else None
        matched_vendor = _matched_vendor_name(vectorstore, case["query"])
        unmatched_vendor = _unmatched_vendor_reference(vectorstore, case["query"])
        probes.append({
            "id": case["id"],
            "kind": "clear_vendor" if expected_vendors else "unknown_vendor",
            "expected_vendors": expected_vendors,
            "top_vendor": top_vendor,
            "top_score": round(float(top_score), 4) if top_score is not None else None,
            "vendor_match": bool(expected_vendors and top_vendor in expected_vendors),
            "retrieval_query": retrieval_query,
            "vendor_bound": "filter" in search_kwargs,
            "matched_vendor": matched_vendor,
            "unmatched_vendor_reference": unmatched_vendor,
            "evidence_gate": (
                "human_review_unrecognized_vendor" if unmatched_vendor else "retrieval_threshold"
            ),
        })
    return probes


def _summary_for_threshold(probes: list[dict], threshold: float) -> dict:
    clear = [probe for probe in probes if probe["kind"] == "clear_vendor"]
    unknown = [probe for probe in probes if probe["kind"] == "unknown_vendor"]
    clear_success = [
        probe for probe in clear
        if probe["vendor_match"] and probe["top_score"] is not None and probe["top_score"] >= threshold
    ]
    raw_unknown_safe = [
        probe for probe in unknown
        if probe["top_score"] is None or probe["top_score"] < threshold
    ]
    evidence_gated_unknown_safe = [
        probe for probe in unknown
        if probe["unmatched_vendor_reference"]
        or probe["top_score"] is None
        or probe["top_score"] < threshold
    ]
    percent = lambda count, total: round(count / total * 100, 1) if total else None
    return {
        "threshold": threshold,
        "clear_vendor_coverage": percent(len(clear_success), len(clear)),
        "clear_vendor_false_abstention_rate": percent(len(clear) - len(clear_success), len(clear)),
        "raw_unknown_vendor_safe_handoff_rate": percent(len(raw_unknown_safe), len(unknown)),
        "raw_unknown_vendor_unsafe_retrieval_rate": percent(
            len(unknown) - len(raw_unknown_safe), len(unknown)
        ),
        "evidence_gated_unknown_vendor_safe_handoff_rate": percent(
            len(evidence_gated_unknown_safe), len(unknown)
        ),
        "evidence_gated_unknown_vendor_unsafe_conclusion_rate": percent(
            len(unknown) - len(evidence_gated_unknown_safe), len(unknown)
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument(
        "--thresholds",
        default=",".join(str(value) for value in DEFAULT_THRESHOLDS),
        help="Comma-separated Chroma relevance thresholds.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cases = _load_cases(args.cases)
    calibration_cases = [
        case for case in cases
        if case.get("expected_source_vendors") or _is_unknown_vendor_case(case)
    ]
    if args.dry_run:
        print(json.dumps({
            "calibration_case_count": len(calibration_cases),
            "clear_vendor_cases": sum(bool(case.get("expected_source_vendors")) for case in calibration_cases),
            "unknown_vendor_cases": sum(_is_unknown_vendor_case(case) for case in calibration_cases),
            "thresholds": _parse_thresholds(args.thresholds),
        }, indent=2))
        return

    probes = _score_cases(calibration_cases)
    thresholds = _parse_thresholds(args.thresholds)
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "fixture_scope": (
            "Synthetic behavioral calibration only. These scores are not calibrated "
            "probabilities and must not be generalized to production contracts."
        ),
        "manifest": str(args.cases),
        "threshold_tradeoffs": [_summary_for_threshold(probes, threshold) for threshold in thresholds],
        "probes": probes,
    }
    results_dir = EVALUATION_DIR / "results"
    results_dir.mkdir(exist_ok=True)
    output = results_dir / f"retrieval-calibration-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({
        "threshold_tradeoffs": report["threshold_tradeoffs"],
        "result_file": str(output),
    }, indent=2))


if __name__ == "__main__":
    main()
