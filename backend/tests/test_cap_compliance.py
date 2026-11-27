"""Unit and fixture checks for deterministic vendor-month cap calculations."""

import csv
import json
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

from app.core.cap_compliance import calculate_monthly_cap_records, cap_record_key  # noqa: E402
from evaluation.evaluate_cap_detection import evaluate_cap_detection  # noqa: E402


class CapComplianceTests(unittest.TestCase):
    def test_only_contract_valid_invoices_are_grouped_by_vendor_month(self):
        contracts = [{
            "vendor_id": 7,
            "vendor_name": "Example Ltd",
            "monthly_cap": 100,
            "effective_date": "2024-01-15",
            "expiry_date": "2024-02-10",
        }]
        invoices = [
            {"vendor_id": 7, "invoice_date": "2024-01-14", "amount_due": 99},
            {"vendor_id": 7, "invoice_date": "2024-01-15", "amount_due": 60},
            {"vendor_id": 7, "invoice_date": "2024-01-31", "amount_due": 60},
            {"vendor_id": 7, "invoice_date": "2024-02-10", "amount_due": 80},
            {"vendor_id": 7, "invoice_date": "2024-02-11", "amount_due": 99},
        ]

        records = calculate_monthly_cap_records(invoices, contracts)

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["month"], "2024-01")
        self.assertEqual(records[0]["monthly_invoiced"], 120.0)
        self.assertTrue(records[0]["is_breach"])
        self.assertEqual(records[1]["monthly_invoiced"], 80.0)
        self.assertFalse(records[1]["is_breach"])

    def test_generated_fixture_has_complete_cap_detection_coverage(self):
        contracts = json.loads((REPOSITORY_ROOT / "data" / "contracts_metadata.json").read_text(encoding="utf-8"))
        manifest = json.loads((REPOSITORY_ROOT / "data" / "seed" / "anomaly_manifest.json").read_text(encoding="utf-8"))
        with (REPOSITORY_ROOT / "data" / "seed" / "invoices.csv").open(newline="", encoding="utf-8") as handle:
            invoices = list(csv.DictReader(handle))

        report = evaluate_cap_detection(invoices, contracts, manifest)

        self.assertEqual(report["ground_truth_breaches"], 30)
        self.assertEqual(report["detected_breaches"], 30)
        self.assertEqual(report["detail_matches"], 30)
        self.assertEqual(report["confusion_matrix"]["false_positive"], 0)
        self.assertEqual(report["confusion_matrix"]["false_negative"], 0)
        self.assertEqual(report["metrics"]["precision"], 1.0)
        self.assertEqual(report["metrics"]["recall"], 1.0)
        self.assertEqual(report["metrics"]["f1"], 1.0)
        baseline = report["naive_average_invoice_baseline"]
        self.assertEqual(baseline["detected_breaches"], 0)
        self.assertEqual(baseline["confusion_matrix"]["true_positive"], 0)
        self.assertEqual(baseline["confusion_matrix"]["false_positive"], 0)
        self.assertEqual(baseline["confusion_matrix"]["false_negative"], 30)
        self.assertEqual(baseline["confusion_matrix"]["true_negative"], 1115)
        self.assertEqual(baseline["metrics"]["precision"], None)
        self.assertEqual(baseline["metrics"]["recall"], 0.0)
        self.assertEqual(baseline["metrics"]["f1"], 0.0)

    def test_manifest_identity_uses_vendor_and_month(self):
        self.assertEqual(cap_record_key({"vendor_id": "7", "month": "2024-01"}), (7, "2024-01"))
