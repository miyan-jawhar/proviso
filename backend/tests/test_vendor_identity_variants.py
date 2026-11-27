"""Regression checks for the published vendor-identity boundary manifest."""

import json
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

from evaluation.evaluate_vendor_identity_variants import evaluate_variant_cases, load_variant_cases  # noqa: E402


class VendorIdentityVariantTests(unittest.TestCase):
    def test_published_variant_boundary_has_no_unsafe_binding_or_unresolved_case(self):
        manifest = load_variant_cases()
        contracts = json.loads((REPOSITORY_ROOT / "data" / "contracts_metadata.json").read_text(encoding="utf-8"))
        report = evaluate_variant_cases(
            manifest["cases"],
            [contract["vendor_name"] for contract in contracts],
        )

        self.assertEqual(report["passed_cases"], report["case_count"])
        self.assertEqual(report["unsafe_binding_rate"], 0.0)
        self.assertEqual(report["unresolved_case_count"], 0)
        self.assertEqual(report["match_acceptance_rate"], 1.0)
        self.assertEqual(report["safe_escalation_rate"], 1.0)
