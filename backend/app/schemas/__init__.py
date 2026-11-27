"""Pydantic v2 Schemas — Auth, Query, Feedback."""

from pydantic import BaseModel, Field
from typing import Literal, Optional
from datetime import datetime


# ── Auth Schemas ───────────────────────────────────────────────────
class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=6, max_length=200)
    full_name: Optional[str] = None

class UserLogin(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class TokenRefresh(BaseModel):
    refresh_token: str

class UserInfo(BaseModel):
    username: str
    full_name: Optional[str]
    role: str


# ── Query Schemas ──────────────────────────────────────────────────
class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000,
                       description="Natural language query")

class SourceCitation(BaseModel):
    filename: str = ""
    page_number: int | str = ""
    vendor_name: str = ""
    clause: str = ""
    relevance_rank: int = 0
    relevance_score: float | None = None


class ReviewEscalation(BaseModel):
    status: Literal["human_review_required"]
    agent: str
    reason_code: str
    reason: str
    confidence: float | None = None
    confidence_threshold: float | None = None
    recommended_action: str
    blocking: bool = True

class RiskScorecard(BaseModel):
    overall_severity: str = "UNKNOWN"
    risk_dimensions: list[dict] = []
    recommended_actions: list[str] = []
    evidence_summary: dict = {}
    synthesis_method: str = "deterministic"


class QueryResponse(BaseModel):
    answer: str
    cited_sources: list[SourceCitation] = []
    sql_queries: list[str] = []
    agent_trace: list[dict] = []
    iterations: int = 0
    error_log: list[str] = []
    review_escalations: list[ReviewEscalation] = []
    risk_scorecard: Optional[RiskScorecard] = None


# ── Feedback Schemas ───────────────────────────────────────────────
class FeedbackCreate(BaseModel):
    query_text: str
    response_text: str
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None

class FeedbackResponse(BaseModel):
    feedback_id: int
    message: str = "Feedback recorded successfully"


# ── Dashboard / Analytics Schemas ──────────────────────────────────
class VendorRiskSummary(BaseModel):
    vendor_id: int
    vendor_name: str
    country: str
    risk_tier: str
    total_invoices: int = 0
    total_amount: float = 0.0
    overdue_count: int = 0

class ComplianceMetrics(BaseModel):
    total_contracts: int
    gdpr_compliant: int
    gdpr_missing: int
    high_penalty_count: int
    compliance_rate: float

class CostAnomaly(BaseModel):
    vendor_name: str
    contract_cap: float
    actual_amount: float
    overpayment: float
    period: str
