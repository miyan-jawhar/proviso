"""Deterministic contract-month cap calculation helpers.

This module deliberately contains no LLM, vector-store, or database code.  It
implements the financial policy used by Proviso's monthly-cap audit so it can
be exercised against the seeded ledger as a reproducible batch check.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Iterable, Mapping


def _as_date(value: Any) -> date:
    """Accept ISO date strings and database date objects at the data boundary."""
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def calculate_monthly_cap_records(
    invoices: Iterable[Mapping[str, Any]], contracts: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Group contract-valid invoices and return one deterministic vendor-month row.

    A vendor-month is eligible only when its invoice date falls inside the
    effective and expiry dates of the contract supplied for that vendor.  The
    caller is responsible for escalating version conflicts; this helper models
    the single executed contract represented by the local fixture corpus.
    """
    contract_by_vendor = {
        int(contract["vendor_id"]): {
            "vendor_name": str(contract["vendor_name"]),
            "monthly_cap": float(contract["monthly_cap"]),
            "effective_date": _as_date(contract["effective_date"]),
            "expiry_date": _as_date(contract["expiry_date"]),
        }
        for contract in contracts
    }
    totals: dict[tuple[int, str], float] = defaultdict(float)
    invoice_counts: dict[tuple[int, str], int] = defaultdict(int)

    for invoice in invoices:
        vendor_id = int(invoice["vendor_id"])
        contract = contract_by_vendor.get(vendor_id)
        if contract is None:
            continue

        invoice_date = _as_date(invoice["invoice_date"])
        if not (contract["effective_date"] <= invoice_date <= contract["expiry_date"]):
            continue

        key = (vendor_id, invoice_date.strftime("%Y-%m"))
        totals[key] += float(invoice["amount_due"])
        invoice_counts[key] += 1

    records: list[dict[str, Any]] = []
    for (vendor_id, month), total in sorted(totals.items(), key=lambda item: item[0]):
        contract = contract_by_vendor[vendor_id]
        monthly_cap = contract["monthly_cap"]
        monthly_invoiced = round(total, 2)
        excess = round(max(monthly_invoiced - monthly_cap, 0.0), 2)
        records.append(
            {
                "vendor_id": vendor_id,
                "vendor_name": contract["vendor_name"],
                "month": month,
                "monthly_cap": monthly_cap,
                "monthly_invoiced": monthly_invoiced,
                "excess": excess,
                "invoice_count": invoice_counts[(vendor_id, month)],
                "is_breach": excess > 0,
            }
        )
    return records


def cap_record_key(record: Mapping[str, Any]) -> tuple[int, str]:
    """Return the stable identifier used for a vendor-month decision."""
    return int(record["vendor_id"]), str(record["month"])
