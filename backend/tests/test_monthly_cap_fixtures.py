"""Regression checks for the synthetic monthly-cap policy."""

import sys
import unittest
from collections import defaultdict
from datetime import date
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.generate_invoices import generate_invoices, load_contract_metadata  # noqa: E402


class MonthlyCapFixtureTests(unittest.TestCase):
    def test_invoice_dates_are_contract_valid_and_breaches_are_controlled(self):
        contracts = load_contract_metadata()
        invoices, manifest = generate_invoices(contracts)
        contracts_by_vendor = {int(contract["vendor_id"]): contract for contract in contracts}
        monthly_totals = defaultdict(float)

        for invoice in invoices:
            contract = contracts_by_vendor[int(invoice["vendor_id"])]
            invoice_date = date.fromisoformat(invoice["invoice_date"])
            self.assertGreaterEqual(invoice_date, date.fromisoformat(contract["effective_date"]))
            self.assertLessEqual(invoice_date, date.fromisoformat(contract["expiry_date"]))
            monthly_totals[(int(invoice["vendor_id"]), invoice["invoice_date"][:7])] += invoice["amount_due"]

        observed = {
            key
            for key, total in monthly_totals.items()
            if total > contracts_by_vendor[key[0]]["monthly_cap"]
        }
        manifest_breaches = {
            (item["vendor_id"], item["month"])
            for item in manifest["observed_monthly_breaches"]
        }

        self.assertEqual(manifest["generator_version"], "monthly-cap-v2")
        self.assertEqual(len(invoices), 10_015)
        self.assertEqual(observed, manifest_breaches)
        self.assertEqual(len(observed), 30)
