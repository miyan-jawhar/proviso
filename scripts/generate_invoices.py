"""Generate vendors and invoices with auditable monthly-cap ground truth.

The contract policy is a maximum *monthly* billing cap.  Earlier fixtures drew
every invoice as a fraction of that cap, which made several ordinary invoices
per month add up to accidental violations.  This generator instead creates a
monthly budget first, distributes it across invoices, and injects a controlled
set of over-cap vendor-months.
"""

from __future__ import annotations

import csv
import json
import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any


RANDOM_SEED = 42
TARGET_BASE_INVOICES = 10_000
MAX_CONTRACT_MONTHS = 24
LOW_CAP_BREACH_MONTHS_PER_VENDOR = 4
NORMAL_BREACH_VENDORS = 5
NORMAL_BREACH_MONTHS_PER_VENDOR = 2
DUPLICATE_PAYMENT_COUNT = 15

DATA_DIR = Path(__file__).parent.parent / "data"
METADATA_FILE = DATA_DIR / "contracts_metadata.json"
VENDORS_CSV = DATA_DIR / "seed" / "vendors.csv"
INVOICES_CSV = DATA_DIR / "seed" / "invoices.csv"
ANOMALY_MANIFEST = DATA_DIR / "seed" / "anomaly_manifest.json"


def load_contract_metadata() -> list[dict[str, Any]]:
    with METADATA_FILE.open(encoding="utf-8") as handle:
        return json.load(handle)


def generate_vendors(contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build vendor records from contract metadata."""
    return [
        {
            "vendor_id": contract["vendor_id"],
            "vendor_name": contract["vendor_name"],
            "country": contract["country"],
            "risk_tier": contract["risk_tier"],
        }
        for contract in contracts
    ]


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _next_month(value: date) -> date:
    return date(value.year + 1, 1, 1) if value.month == 12 else date(value.year, value.month + 1, 1)


def _contract_months(contract: dict[str, Any]) -> list[dict[str, date]]:
    """Return up to 24 calendar months that overlap the contract term."""
    effective_date = date.fromisoformat(contract["effective_date"])
    expiry_date = date.fromisoformat(contract["expiry_date"])
    current = _month_start(effective_date)
    months: list[dict[str, date]] = []

    while current <= expiry_date and len(months) < MAX_CONTRACT_MONTHS:
        next_month = _next_month(current)
        period_start = max(current, effective_date)
        period_end = min(next_month - timedelta(days=1), expiry_date)
        if period_start <= period_end:
            months.append({"month": current, "start": period_start, "end": period_end})
        current = next_month

    return months


def _build_schedule(contracts: list[dict[str, Any]], target_count: int) -> list[dict[str, Any]]:
    """Allocate approximately target_count invoices across contract-valid months."""
    periods = [
        {"contract": contract, **month}
        for contract in contracts
        for month in _contract_months(contract)
    ]
    if not periods:
        raise ValueError("No contract-valid months were available for invoice generation.")

    base_count, remainder = divmod(target_count, len(periods))
    for index, period in enumerate(periods):
        period["invoice_count"] = base_count + (1 if index < remainder else 0)
    return periods


def _select_breach_periods(
    schedule: list[dict[str, Any]], rng: random.Random
) -> set[tuple[int, str]]:
    """Choose the known monthly-cap violations before amounts are generated."""
    periods_by_vendor: dict[int, list[dict[str, Any]]] = defaultdict(list)
    contracts_by_vendor: dict[int, dict[str, Any]] = {}
    for period in schedule:
        vendor_id = int(period["contract"]["vendor_id"])
        periods_by_vendor[vendor_id].append(period)
        contracts_by_vendor[vendor_id] = period["contract"]

    breaches: set[tuple[int, str]] = set()
    for vendor_id, contract in contracts_by_vendor.items():
        if contract["contract_type"] != "low_cap":
            continue
        selected = rng.sample(
            periods_by_vendor[vendor_id],
            min(LOW_CAP_BREACH_MONTHS_PER_VENDOR, len(periods_by_vendor[vendor_id])),
        )
        breaches.update((vendor_id, period["month"].isoformat()) for period in selected)

    normal_vendor_ids = [
        vendor_id
        for vendor_id, contract in contracts_by_vendor.items()
        if contract["contract_type"] == "normal"
    ]
    for vendor_id in rng.sample(normal_vendor_ids, min(NORMAL_BREACH_VENDORS, len(normal_vendor_ids))):
        selected = rng.sample(
            periods_by_vendor[vendor_id],
            min(NORMAL_BREACH_MONTHS_PER_VENDOR, len(periods_by_vendor[vendor_id])),
        )
        breaches.update((vendor_id, period["month"].isoformat()) for period in selected)

    return breaches


def _distribute_monthly_total(total: float, count: int, rng: random.Random) -> list[float]:
    """Split a monthly budget into invoice amounts while preserving its total."""
    total_cents = round(total * 100)
    weights = [rng.uniform(0.6, 1.4) for _ in range(count)]
    weight_sum = sum(weights)
    amounts = [round(total_cents * weight / weight_sum) for weight in weights[:-1]]
    amounts.append(total_cents - sum(amounts))
    return [round(amount / 100, 2) for amount in amounts]


def _payment_status(vendor_id: int, overdue_vendors: set[int], rng: random.Random) -> str:
    if vendor_id in overdue_vendors and rng.random() < 0.40:
        return "OVERDUE"
    if rng.random() < 0.05:
        return "OVERDUE"
    if rng.random() < 0.15:
        return "PENDING"
    return "PAID"


def _observed_monthly_breaches(
    invoices: list[dict[str, Any]], contracts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    caps = {int(contract["vendor_id"]): float(contract["monthly_cap"]) for contract in contracts}
    names = {int(contract["vendor_id"]): contract["vendor_name"] for contract in contracts}
    totals: dict[tuple[int, str], float] = defaultdict(float)
    for invoice in invoices:
        key = (int(invoice["vendor_id"]), invoice["invoice_date"][:7])
        totals[key] += float(invoice["amount_due"])

    return [
        {
            "vendor_id": vendor_id,
            "vendor_name": names[vendor_id],
            "month": month,
            "monthly_cap": caps[vendor_id],
            "monthly_invoiced": round(total, 2),
            "excess": round(total - caps[vendor_id], 2),
        }
        for (vendor_id, month), total in sorted(totals.items())
        if total > caps[vendor_id]
    ]


def generate_invoices(
    contracts: list[dict[str, Any]], target_count: int = TARGET_BASE_INVOICES
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Generate invoices and an auditable monthly-cap anomaly manifest."""
    rng = random.Random(RANDOM_SEED)
    schedule = _build_schedule(contracts, target_count)
    breach_periods = _select_breach_periods(schedule, rng)
    overdue_vendors = set(rng.sample([int(c["vendor_id"]) for c in contracts], min(3, len(contracts))))

    invoices: list[dict[str, Any]] = []
    invoice_id = 1
    for period in schedule:
        contract = period["contract"]
        vendor_id = int(contract["vendor_id"])
        month_key = period["month"].isoformat()
        cap = float(contract["monthly_cap"])
        is_breach = (vendor_id, month_key) in breach_periods
        monthly_total = cap * rng.uniform(1.25, 1.60) if is_breach else cap * rng.uniform(0.55, 0.85)

        for amount in _distribute_monthly_total(monthly_total, int(period["invoice_count"]), rng):
            days_in_period = (period["end"] - period["start"]).days
            invoice_date = period["start"] + timedelta(days=rng.randint(0, days_in_period))
            invoices.append({
                "invoice_id": invoice_id,
                "vendor_id": vendor_id,
                "invoice_date": invoice_date.isoformat(),
                "amount_due": amount,
                "payment_status": _payment_status(vendor_id, overdue_vendors, rng),
            })
            invoice_id += 1

    duplicate_sources = rng.sample(invoices, min(DUPLICATE_PAYMENT_COUNT, len(invoices)))
    duplicate_signatures = []
    for source in duplicate_sources:
        duplicate = dict(source)
        duplicate["invoice_id"] = invoice_id
        invoices.append(duplicate)
        duplicate_signatures.append({
            "vendor_id": duplicate["vendor_id"],
            "invoice_date": duplicate["invoice_date"],
            "amount_due": duplicate["amount_due"],
        })
        invoice_id += 1

    rng.shuffle(invoices)
    for index, invoice in enumerate(invoices, 1):
        invoice["invoice_id"] = index

    observed_breaches = _observed_monthly_breaches(invoices, contracts)
    manifest = {
        "generator_version": "monthly-cap-v2",
        "random_seed": RANDOM_SEED,
        "monthly_cap_policy": (
            "A breach occurs when the sum of a vendor's invoices in a contract-valid "
            "calendar month exceeds that vendor's monthly cap."
        ),
        "base_invoice_count": target_count,
        "total_invoice_count": len(invoices),
        "controlled_breach_periods": len(breach_periods),
        "observed_monthly_breaches": observed_breaches,
        "duplicate_payment_signatures": duplicate_signatures,
        "overdue_vendor_ids": sorted(overdue_vendors),
    }
    return invoices, manifest


def main() -> None:
    print("Loading contract metadata...")
    contracts = load_contract_metadata()
    vendors = generate_vendors(contracts)
    invoices, manifest = generate_invoices(contracts)

    VENDORS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with VENDORS_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["vendor_id", "vendor_name", "country", "risk_tier"])
        writer.writeheader()
        writer.writerows(vendors)
    with INVOICES_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["invoice_id", "vendor_id", "invoice_date", "amount_due", "payment_status"])
        writer.writeheader()
        writer.writerows(invoices)
    with ANOMALY_MANIFEST.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)

    statuses = defaultdict(int)
    caps = {int(contract["vendor_id"]): float(contract["monthly_cap"]) for contract in contracts}
    individual_over_cap = 0
    for invoice in invoices:
        statuses[invoice["payment_status"]] += 1
        individual_over_cap += float(invoice["amount_due"]) > caps[int(invoice["vendor_id"])]

    print("\n" + "=" * 60)
    print(f"  Generated {len(vendors)} vendors -> data/seed/vendors.csv")
    print(f"  Generated {len(invoices)} invoices -> data/seed/invoices.csv")
    print(f"  Status breakdown: {dict(statuses)}")
    print(f"  Monthly cap breach periods: {len(manifest['observed_monthly_breaches'])}")
    print(f"  Individual invoices above a monthly cap: {individual_over_cap}")
    print(f"  Duplicate payment signatures: {len(manifest['duplicate_payment_signatures'])}")
    print("  Ground truth manifest: data/seed/anomaly_manifest.json")
    print("=" * 60)


if __name__ == "__main__":
    main()
