"""
Health & Analytics Routes
==========================
Health check + dashboard data endpoints.
"""

import json
from pathlib import Path
from fastapi import APIRouter
from app.config import DATA_DIR, get_settings

router = APIRouter(tags=["System"])

METADATA_FILE = DATA_DIR / "contracts_metadata.json"
LEDGER_FILE = DATA_DIR / "enterprise_ledger.db"
INVOICES_FILE = DATA_DIR / "seed" / "invoices.csv"
MANIFEST_FILE = DATA_DIR / "seed" / "anomaly_manifest.json"


def _reasoning_status(settings) -> dict:
    """Describe configuration without leaking provider credentials."""
    provider = settings.llm_provider.strip().lower()
    if provider == "gemini":
        configured = bool(settings.gemini_api_key)
        execution = "hosted"
    elif provider == "openai":
        configured = bool(settings.openai_api_key)
        execution = "hosted"
    elif provider == "ollama":
        # A configured model does not prove the local Ollama service is running;
        # the endpoint intentionally reports configuration, not a network probe.
        configured = bool(settings.ollama_model)
        execution = "local"
    else:
        configured = False
        execution = "unconfigured"

    return {
        "configured_provider": provider,
        "execution": execution,
        "open_ended_reasoning_configured": configured,
        "service_verified": False if provider == "ollama" else None,
    }


def _deterministic_audit_status() -> dict:
    """Report whether the local files needed for the bounded cap audit exist."""
    ledger_available = LEDGER_FILE.exists() or INVOICES_FILE.exists()
    contract_metadata_available = METADATA_FILE.exists()
    return {
        "execution": "local",
        "available": ledger_available and contract_metadata_available,
        "ledger_available": ledger_available,
        "contract_metadata_available": contract_metadata_available,
        "ground_truth_manifest_available": MANIFEST_FILE.exists(),
    }


@router.get("/health")
async def health_check():
    """System health check."""
    settings = get_settings()
    return {
        "status": "healthy",
        "service": "Proviso",
        "version": "1.0.0",
        "operating_mode": {
            "open_ended_reasoning": _reasoning_status(settings),
            "deterministic_cap_audit": _deterministic_audit_status(),
        },
    }


@router.get("/analytics/compliance")
async def compliance_metrics():
    """Return compliance metrics from contract metadata."""
    if not METADATA_FILE.exists():
        return {"error": "Contract metadata not found. Run data generation first."}

    with open(METADATA_FILE) as f:
        contracts = json.load(f)

    total = len(contracts)
    gdpr_ok = sum(1 for c in contracts if c.get("include_gdpr", True))
    gdpr_missing = total - gdpr_ok
    high_penalty = sum(1 for c in contracts if c.get("late_fee_pct", 0) > 10)

    return {
        "total_contracts": total,
        "gdpr_compliant": gdpr_ok,
        "gdpr_missing": gdpr_missing,
        "high_penalty_count": high_penalty,
        "compliance_rate": round(gdpr_ok / total * 100, 1) if total else 0,
    }


@router.get("/analytics/vendors")
async def vendor_summary():
    """Return vendor risk summary from contract metadata."""
    if not METADATA_FILE.exists():
        return []

    with open(METADATA_FILE) as f:
        contracts = json.load(f)

    vendors = []
    for c in contracts:
        vendors.append({
            "vendor_id": c["vendor_id"],
            "vendor_name": c["vendor_name"],
            "country": c["country"],
            "risk_tier": c.get("risk_tier", "UNKNOWN"),
            "monthly_cap": c.get("monthly_cap", 0),
            "late_fee_pct": c.get("late_fee_pct", 0),
            "has_gdpr": c.get("include_gdpr", True),
            "contract_type": c.get("contract_type", "normal"),
        })

    return vendors


@router.get("/analytics/anomalies")
async def cost_anomalies():
    """Return detected cost anomalies (contracts with low caps or high penalties)."""
    if not METADATA_FILE.exists():
        return []

    with open(METADATA_FILE) as f:
        contracts = json.load(f)

    anomalies = []
    for c in contracts:
        issues = []
        if c.get("late_fee_pct", 0) > 10:
            issues.append(f"High penalty: {c['late_fee_pct']}%")
        if not c.get("include_gdpr", True):
            issues.append("Missing GDPR clause")
        if c.get("contract_type") == "low_cap":
            issues.append(f"Low monthly cap: INR {c['monthly_cap']:,}")

        if issues:
            anomalies.append({
                "vendor_id": c["vendor_id"],
                "vendor_name": c["vendor_name"],
                "contract_number": c["contract_number"],
                "issues": issues,
                "risk_tier": c.get("risk_tier", "UNKNOWN"),
            })

    return anomalies
