"""
Real-Contract Extraction Evaluation
=====================================
Evaluates the generalised MONTHLY_CAP_PATTERN and legal extraction pipeline
against multi-currency contract fixtures (USD, GBP, EUR, INR).

IMPORTANT: These are demonstration fixtures modelled on public-domain contract
structures, not actual contracts. Results document extraction behaviour on
realistic multi-currency text; they are NOT evidence of production accuracy.

Usage:
    python evaluation/evaluate_real_contracts.py
    python evaluation/evaluate_real_contracts.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_PATH = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_PATH))

REAL_CONTRACTS_DIR = PROJECT_ROOT / "data" / "real_contracts"
REAL_METADATA_FILE = PROJECT_ROOT / "data" / "real_contracts_metadata.json"
RESULTS_DIR = PROJECT_ROOT / "evaluation" / "results"

try:
    from app.agents.legal_agent import MONTHLY_CAP_PATTERN, _extract_cap_amount
    _PATTERN_IMPORTED = True
except ImportError:
    _PATTERN_IMPORTED = False
    # Fallback inline pattern for standalone / dry-run use
    MONTHLY_CAP_PATTERN = re.compile(
        r"""
        (?:
            (?:maximum\s+|not[\s\-]to[\s\-]exceed\s+)?
            (?:monthly\s+)?
            (?:billing\s+|service\s+|payment\s+|not[\s\-]to[\s\-]exceed\s+)?
            (?:cap|limit|fee|amount|ceiling)
            \s+of\s+
            (?:INR|USD|GBP|EUR|AUD|CAD)?\s*
            (?:£|\$|€|₹|Rs\.?)\s*
            ([0-9][0-9,]*(?:\.[0-9]+)?)
            (?:\s*(?:thousand|million|k|M|lakh|crore))?
        |
            not[\s\-]to[\s\-]exceed\s+
            (?:INR|USD|GBP|EUR|AUD|CAD)?\s*
            (?:£|\$|€|₹|Rs\.?)?\s*
            ([0-9][0-9,]*(?:\.[0-9]+)?)
            (?:\s*(?:thousand|million|k|M|lakh|crore))?
            (?:\s+per\s+month)?
        |
            (?:maximum\s+)?monthly\s+(?:billing\s+)?cap\s+of\s+
            (?:INR|USD|GBP|EUR)?\s*
            (?:£|\$|€|₹|Rs\.?)?\s*
            ([0-9][0-9,]*(?:\.[0-9]+)?)
        )
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    def _extract_cap_amount(match):
        for group in match.groups():
            if group:
                try:
                    return int(float(group.replace(",", "")))
                except ValueError:
                    return None
        return None


def _detect_gdpr(text: str) -> bool:
    return bool(re.search(
        r"\b(gdpr|data\s+processing\s+agreement|dpa|data\s+protection\s+act"
        r"|uk\s+gdpr|ccpa|fisma|personal\s+data\s+protection"
        r"|auftragsverarbeitungsvertrag)\b",
        text, re.IGNORECASE,
    ))


def _extract_late_fee(text: str) -> float | None:
    match = re.search(
        r"(?:late\s+(?:payment\s+)?(?:fee|penalty|interest)|interest\s+at)\s+"
        r"(?:a\s+rate\s+of\s+)?(\d+(?:\.\d+)?)\s*%",
        text, re.IGNORECASE,
    )
    # Also match statutory rate format "8% per annum"
    if not match:
        match = re.search(r"(\d+(?:\.\d+)?)\s*%\s+per\s+(?:annum|month)", text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def evaluate_contract(filepath: Path, metadata: dict, dry_run: bool = False) -> dict:
    """Run all extraction tests against one contract file."""
    result = {
        "filename": filepath.name,
        "vendor_name": metadata.get("vendor_name", "Unknown"),
        "currency": metadata.get("currency", "?"),
        "expected_cap": metadata.get("monthly_cap"),
        "expected_gdpr": metadata.get("has_gdpr_equivalent"),
        "expected_late_fee": metadata.get("late_payment_rate"),
    }

    if dry_run:
        result["dry_run"] = True
        return result

    if not filepath.exists():
        result["error"] = f"File not found: {filepath}"
        return result

    text = filepath.read_text(encoding="utf-8", errors="replace")

    # Cap extraction
    matches = list(MONTHLY_CAP_PATTERN.finditer(text))
    extracted_caps = []
    for m in matches:
        amount = _extract_cap_amount(m)
        if amount:
            extracted_caps.append(amount)

    expected_cap = metadata.get("monthly_cap")
    cap_hit = expected_cap in extracted_caps if extracted_caps else False
    result["extracted_caps"] = extracted_caps
    result["cap_extraction_pass"] = cap_hit
    result["cap_extraction_note"] = (
        "PASS" if cap_hit
        else f"FAIL — expected {expected_cap}, got {extracted_caps or 'nothing'}"
    )

    # GDPR detection
    gdpr_found = _detect_gdpr(text)
    expected_gdpr = metadata.get("has_gdpr_equivalent", False)
    result["gdpr_detected"] = gdpr_found
    result["gdpr_extraction_pass"] = gdpr_found == expected_gdpr

    # Late fee extraction
    late_fee = _extract_late_fee(text)
    expected_fee = metadata.get("late_payment_rate")
    result["late_fee_detected"] = late_fee
    result["late_fee_extraction_pass"] = (
        late_fee is not None and abs(late_fee - expected_fee) < 0.5
        if expected_fee is not None else late_fee is None
    )

    return result


def main():
    parser = argparse.ArgumentParser(description="Evaluate real-contract extraction")
    parser.add_argument("--dry-run", action="store_true", help="Skip file I/O, test imports only")
    args = parser.parse_args()

    print("=" * 70)
    print("Real-Contract Extraction Evaluation")
    print(f"Pattern imported from legal_agent: {_PATTERN_IMPORTED}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'FULL EVALUATION'}")
    print("=" * 70)
    print()
    print("DISCLAIMER: Fixtures are demonstration models of public-domain")
    print("contract structures, not actual contracts. Results are extraction")
    print("behaviour tests, not evidence of production accuracy.")
    print()

    if not REAL_METADATA_FILE.exists():
        print(f"ERROR: Metadata file not found: {REAL_METADATA_FILE}")
        sys.exit(1)

    with REAL_METADATA_FILE.open(encoding="utf-8") as f:
        metadata_list = json.load(f)

    results = []
    for meta in metadata_list:
        filepath = REAL_CONTRACTS_DIR / meta["filename"]
        result = evaluate_contract(filepath, meta, dry_run=args.dry_run)
        results.append(result)

        if args.dry_run:
            print(f"  [DRY] {meta['vendor_name']} ({meta.get('currency', '?')})")
            continue

        status = "✓" if result.get("cap_extraction_pass") else "✗"
        print(f"{status} {meta['vendor_name']} ({meta.get('currency', '?')})")
        print(f"  Cap: {result.get('cap_extraction_note', 'N/A')}")
        gdpr_pass = result.get("gdpr_extraction_pass")
        print(f"  GDPR: {'PASS' if gdpr_pass else 'FAIL'}")
        fee_pass = result.get("late_fee_extraction_pass")
        print(f"  Late fee: {'PASS' if fee_pass else 'FAIL'} "
              f"(detected: {result.get('late_fee_detected')})")
        print()

    if not args.dry_run:
        total = len(results)
        cap_pass = sum(1 for r in results if r.get("cap_extraction_pass"))
        gdpr_pass = sum(1 for r in results if r.get("gdpr_extraction_pass"))
        fee_pass = sum(1 for r in results if r.get("late_fee_extraction_pass"))

        print("=" * 70)
        print("SUMMARY")
        print(f"  Contracts evaluated:        {total}")
        print(f"  Cap extraction recall:      {cap_pass}/{total} "
              f"({cap_pass / total:.0%})" if total else "  Cap extraction recall: N/A")
        print(f"  GDPR detection accuracy:    {gdpr_pass}/{total} "
              f"({gdpr_pass / total:.0%})" if total else "  GDPR detection accuracy: N/A")
        print(f"  Late-fee extraction recall: {fee_pass}/{total} "
              f"({fee_pass / total:.0%})" if total else "  Late-fee recall: N/A")
        print()
        print("NOTE: Non-100% results indicate genuine extraction failure modes,")
        print("not test suite errors. These are documented for transparency.")
        print("=" * 70)

        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_file = RESULTS_DIR / f"real-contracts-{timestamp}.json"
        with output_file.open("w", encoding="utf-8") as f:
            json.dump({
                "timestamp": timestamp,
                "disclaimer": (
                    "Demonstration fixtures only. "
                    "Not evidence of production accuracy."
                ),
                "summary": {
                    "total": total,
                    "cap_extraction_recall": f"{cap_pass}/{total}",
                    "gdpr_detection_accuracy": f"{gdpr_pass}/{total}",
                    "late_fee_recall": f"{fee_pass}/{total}",
                },
                "results": results,
            }, f, indent=2)
        print(f"\nResults saved to: {output_file.name}")
    else:
        print("Dry-run complete. No file I/O performed.")


if __name__ == "__main__":
    main()
