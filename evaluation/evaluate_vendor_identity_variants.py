"""Measure conservative vendor-identity binding over named adversarial variants.

The check uses the exact metadata gate used by the Legal Agent.  It does not
call an LLM or semantic vector retrieval: a false legal-entity binding must be
detected before either of those systems can treat a contract as evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from app.agents.legal_agent import _matched_vendor_name, _unmatched_vendor_reference  # noqa: E402


DATA_DIR = REPOSITORY_ROOT / "data"
CONTRACTS_FILE = DATA_DIR / "contracts_metadata.json"
VARIANT_FILE = Path(__file__).parent / "vendor_identity_variants.json"
RESULTS_DIR = Path(__file__).parent / "results"


class _MetadataStore:
    """Minimal Chroma-compatible metadata inventory for deterministic gating."""

    def __init__(self, vendor_names: list[str]):
        self._metadatas = [{"vendor_name": vendor_name} for vendor_name in vendor_names]

    def get(self, include: list[str]):
        return {"metadatas": self._metadatas}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_variant_cases(path: Path = VARIANT_FILE) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    cases = payload.get("cases", [])
    if not cases:
        raise ValueError("The variant manifest must contain at least one case.")
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Variant case ids must be unique.")
    return payload


def evaluate_variant_cases(cases: list[dict[str, Any]], vendor_names: list[str]) -> dict[str, Any]:
    """Score exact matches, safe handoffs, false binds, and unresolved cases."""
    store = _MetadataStore(vendor_names)
    results: list[dict[str, Any]] = []
    for case in cases:
        matched_vendor = _matched_vendor_name(store, case["query"])
        review_candidate = _unmatched_vendor_reference(store, case["query"])
        if matched_vendor:
            observed = "match"
        elif review_candidate:
            observed = "human_review"
        else:
            observed = "unresolved"

        expected = case["expected_behavior"]
        passed = observed == expected
        if expected == "match":
            passed = passed and matched_vendor == case["expected_vendor"]
        results.append({
            "id": case["id"],
            "query": case["query"],
            "expected_behavior": expected,
            "expected_vendor": case.get("expected_vendor"),
            "observed_behavior": observed,
            "matched_vendor": matched_vendor,
            "review_candidate": review_candidate,
            "passed": passed,
        })

    expected_matches = [result for result in results if result["expected_behavior"] == "match"]
    expected_reviews = [result for result in results if result["expected_behavior"] == "human_review"]
    false_binds = [
        result for result in expected_reviews if result["observed_behavior"] == "match"
    ]
    unresolved = [result for result in results if result["observed_behavior"] == "unresolved"]
    return {
        "case_count": len(results),
        "passed_cases": sum(result["passed"] for result in results),
        "match_acceptance_rate": round(
            sum(result["passed"] for result in expected_matches) / len(expected_matches), 6
        ) if expected_matches else None,
        "safe_escalation_rate": round(
            sum(result["passed"] for result in expected_reviews) / len(expected_reviews), 6
        ) if expected_reviews else None,
        "unsafe_binding_rate": round(len(false_binds) / len(expected_reviews), 6) if expected_reviews else None,
        "unresolved_case_count": len(unresolved),
        "false_binding_cases": false_binds,
        "unresolved_cases": unresolved,
        "case_results": results,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write a report to this path instead of evaluation/results.")
    parser.add_argument("--dry-run", action="store_true", help="Print the metric summary without writing a report.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    missing = [str(path) for path in (CONTRACTS_FILE, VARIANT_FILE) if not path.exists()]
    if missing:
        raise SystemExit("Missing required input(s): " + ", ".join(missing))

    manifest = load_variant_cases()
    contracts = json.loads(CONTRACTS_FILE.read_text(encoding="utf-8"))
    report = evaluate_variant_cases(
        manifest["cases"],
        [contract["vendor_name"] for contract in contracts],
    )
    report.update({
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_type": "synthetic_entity_gate_boundary",
        "protocol": manifest["protocol"],
        "inputs": {
            "contracts_sha256": _sha256(CONTRACTS_FILE),
            "variant_manifest_sha256": _sha256(VARIANT_FILE),
        },
        "limitations": [
            "The variants are hand-authored synthetic probes, not a representative sample of enterprise master-data quality.",
            "The current policy intentionally favors safe escalation over fuzzy matching; approved alias resolution requires a governed alias registry that Proviso does not yet implement.",
            "This evaluates entity binding before semantic retrieval and does not measure downstream retrieval quality.",
        ],
    })
    print(
        "Vendor identity variants: "
        f"passed={report['passed_cases']}/{report['case_count']} "
        f"match_acceptance={report['match_acceptance_rate']} "
        f"safe_escalation={report['safe_escalation_rate']} "
        f"unsafe_binding={report['unsafe_binding_rate']} "
        f"unresolved={report['unresolved_case_count']}"
    )
    if args.dry_run:
        return 0

    output = args.output
    if output is None:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        output = RESULTS_DIR / f"vendor-identity-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote report: {output.relative_to(REPOSITORY_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
