"""Evaluate deterministic monthly-cap detection across the complete local ledger.

This is a fixture-consistency check, not an independent model benchmark.  It
compares a fresh contract-valid vendor-month aggregation with the ground truth
manifest emitted by ``scripts/generate_invoices.py``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "backend"))

from app.core.cap_compliance import calculate_monthly_cap_records, cap_record_key  # noqa: E402


DATA_DIR = REPOSITORY_ROOT / "data"
DEFAULT_DATABASE = DATA_DIR / "enterprise_ledger.db"
DEFAULT_INVOICES = DATA_DIR / "seed" / "invoices.csv"
CONTRACTS_FILE = DATA_DIR / "contracts_metadata.json"
MANIFEST_FILE = DATA_DIR / "seed" / "anomaly_manifest.json"
RESULTS_DIR = Path(__file__).parent / "results"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _load_csv_invoices(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _load_sqlite_invoices(path: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT invoice_id, vendor_id, invoice_date, amount_due FROM invoices"
        ).fetchall()
    return [
        {
            "invoice_id": row[0],
            "vendor_id": row[1],
            "invoice_date": row[2],
            "amount_due": row[3],
        }
        for row in rows
    ]


def _metric(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _money_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(
        abs(float(actual[field]) - float(expected[field])) <= 0.01
        for field in ("monthly_cap", "monthly_invoiced", "excess")
    )


def _score_breach_predictions(
    predicted_keys: set[tuple[int, str]],
    expected_keys: set[tuple[int, str]],
    eligible_keys: set[tuple[int, str]],
) -> dict[str, dict[str, int] | dict[str, float | None]]:
    """Calculate classification metrics for one vendor-month decision policy."""
    true_positive = predicted_keys & expected_keys
    false_positive = predicted_keys - expected_keys
    false_negative = expected_keys - predicted_keys
    true_negative = eligible_keys - (predicted_keys | expected_keys)
    return {
        "confusion_matrix": {
            "true_positive": len(true_positive),
            "false_positive": len(false_positive),
            "false_negative": len(false_negative),
            "true_negative": len(true_negative),
        },
        "metrics": {
            "precision": _metric(len(true_positive), len(true_positive) + len(false_positive)),
            "recall": _metric(len(true_positive), len(true_positive) + len(false_negative)),
            "f1": _metric(2 * len(true_positive), 2 * len(true_positive) + len(false_positive) + len(false_negative)),
            "specificity": _metric(len(true_negative), len(true_negative) + len(false_positive)),
            "breach_prevalence": _metric(len(expected_keys), len(eligible_keys)),
        },
    }


def _naive_average_invoice_prediction_keys(records: list[dict[str, Any]]) -> set[tuple[int, str]]:
    """Return the deliberately flawed average-invoice baseline used in Proof View.

    It calculates one mean contract-valid invoice amount per vendor and compares
    it with that vendor's *monthly* cap.  The units are incompatible, which is
    precisely why this is a baseline to reject rather than a production policy.
    """
    vendor_totals: dict[int, float] = {}
    vendor_counts: dict[int, int] = {}
    vendor_caps: dict[int, float] = {}
    for record in records:
        vendor_id = int(record["vendor_id"])
        vendor_totals[vendor_id] = vendor_totals.get(vendor_id, 0.0) + float(record["monthly_invoiced"])
        vendor_counts[vendor_id] = vendor_counts.get(vendor_id, 0) + int(record["invoice_count"])
        vendor_caps[vendor_id] = float(record["monthly_cap"])

    over_cap_vendors = {
        vendor_id
        for vendor_id, total in vendor_totals.items()
        if total / vendor_counts[vendor_id] > vendor_caps[vendor_id]
    }
    return {
        cap_record_key(record)
        for record in records
        if int(record["vendor_id"]) in over_cap_vendors
    }


def evaluate_cap_detection(
    invoices: Iterable[dict[str, Any]],
    contracts: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Return complete-ledger breach metrics against the generated manifest."""
    records = calculate_monthly_cap_records(invoices, contracts)
    actual = {cap_record_key(record): record for record in records if record["is_breach"]}
    expected = {
        cap_record_key(record): record
        for record in manifest.get("observed_monthly_breaches", [])
    }
    eligible = {cap_record_key(record) for record in records}
    expected_keys = set(expected)
    actual_keys = set(actual)
    actual_score = _score_breach_predictions(actual_keys, expected_keys, eligible)
    naive_average_keys = _naive_average_invoice_prediction_keys(records)
    naive_average_score = _score_breach_predictions(naive_average_keys, expected_keys, eligible)
    true_positive = actual_keys & expected_keys
    false_positive = actual_keys - expected_keys
    false_negative = expected_keys - actual_keys
    detail_matches = sum(_money_matches(actual[key], expected[key]) for key in true_positive)

    return {
        "eligible_vendor_months": len(eligible),
        "contract_valid_invoices": sum(record["invoice_count"] for record in records),
        **actual_score,
        "metrics": {
            **actual_score["metrics"],
            "matched_breach_detail_rate": _metric(detail_matches, len(true_positive)),
        },
        "ground_truth_breaches": len(expected),
        "detected_breaches": len(actual),
        "detail_matches": detail_matches,
        "false_positive_records": [actual[key] for key in sorted(false_positive)],
        "false_negative_records": [expected[key] for key in sorted(false_negative)],
        "detected_breach_records": [actual[key] for key in sorted(actual)],
        "naive_average_invoice_baseline": {
            "method": (
                "For each vendor, compare the mean contract-valid invoice amount with the "
                "monthly contract cap, then label every vendor-month for that vendor. This "
                "is an intentionally invalid units comparison retained as a demonstration baseline."
            ),
            "detected_breaches": len(naive_average_keys),
            **naive_average_score,
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--database", type=Path, default=DEFAULT_DATABASE, help="Seeded SQLite ledger to audit (default).")
    source.add_argument("--invoices-csv", type=Path, help="Use generated invoice CSV instead of SQLite.")
    parser.add_argument("--output", type=Path, help="Write a report to this path instead of evaluation/results.")
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs and print metrics without writing a report.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    required_paths = [CONTRACTS_FILE, MANIFEST_FILE]
    source_path = args.invoices_csv or args.database
    required_paths.append(source_path)
    missing = [str(path) for path in required_paths if not path.exists()]
    if missing:
        raise SystemExit("Missing required input(s): " + ", ".join(missing))

    contracts = _load_json(CONTRACTS_FILE)
    manifest = _load_json(MANIFEST_FILE)
    invoices = _load_csv_invoices(args.invoices_csv) if args.invoices_csv else _load_sqlite_invoices(args.database)
    report = evaluate_cap_detection(invoices, contracts, manifest)
    report.update(
        {
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "evaluation_type": "synthetic_fixture_consistency",
            "policy": manifest.get("monthly_cap_policy"),
            "generator_version": manifest.get("generator_version"),
            "input": {
                "ledger_source": str(source_path.relative_to(REPOSITORY_ROOT)),
                "ledger_sha256": _sha256(source_path),
                "contracts_sha256": _sha256(CONTRACTS_FILE),
                "manifest_sha256": _sha256(MANIFEST_FILE),
            },
            "limitations": [
                "The ledger, contracts, labels, and detection policy are all synthetic fixtures.",
                "Precision and recall here are regression metrics over known generated labels, not independent real-world performance.",
                "This check assumes one supplied executed contract per vendor and does not resolve amendments or contract-version conflicts.",
            ],
        }
    )

    metrics = report["metrics"]
    matrix = report["confusion_matrix"]
    print(
        "Cap detection: "
        f"TP={matrix['true_positive']} FP={matrix['false_positive']} "
        f"FN={matrix['false_negative']} TN={matrix['true_negative']} | "
        f"precision={metrics['precision']} recall={metrics['recall']} f1={metrics['f1']}"
    )
    baseline = report["naive_average_invoice_baseline"]
    baseline_matrix = baseline["confusion_matrix"]
    baseline_metrics = baseline["metrics"]
    print(
        "Naive average-invoice baseline: "
        f"TP={baseline_matrix['true_positive']} FP={baseline_matrix['false_positive']} "
        f"FN={baseline_matrix['false_negative']} TN={baseline_matrix['true_negative']} | "
        f"precision={baseline_metrics['precision']} recall={baseline_metrics['recall']} "
        f"f1={baseline_metrics['f1']}"
    )
    if args.dry_run:
        return 0

    output = args.output
    if output is None:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        output = RESULTS_DIR / f"cap-detection-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print(f"Wrote report: {output.relative_to(REPOSITORY_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
