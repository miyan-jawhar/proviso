"""
Proviso — Terms. Spend. Proof.
FastAPI Application Entry Point
==================================
"""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from app.config import get_settings
from app.core.persistence import ensure_persistence_schema
from app.core.rate_limit import limiter
from app.routes import auth, query, feedback, health

# ── Logging ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("proviso")


# ── Lifespan ───────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    settings = get_settings()
    await ensure_persistence_schema(settings)
    db_display = settings.database_url_sync[:50].encode(
        "ascii", "backslashreplace"
    ).decode("ascii")
    logger.info("=" * 60)
    logger.info(f"  {settings.app_name} v{settings.app_version}")
    logger.info(f"  LLM Provider: {settings.llm_provider}")
    logger.info(f"  Database: {db_display}...")
    logger.info(f"  ChromaDB: {'local' if settings.use_local_chroma else f'{settings.chroma_host}:{settings.chroma_port}'}")
    logger.info(f"  Tracing: {'enabled' if settings.langchain_tracing_v2 else 'disabled'}")
    logger.info("=" * 60)
    yield
    logger.info("Application shutting down.")


# ── App ────────────────────────────────────────────────────────────
app = FastAPI(
    title="Proviso — Terms. Spend. Proof.",
    description=(
        "AI-powered enterprise platform for vendor contract compliance auditing "
        "and financial anomaly detection. Uses LangGraph multi-agent orchestration "
        "with specialized Legal (RAG) and Finance (Text-to-SQL) agents."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── CORS ───────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request Logging Middleware ─────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every request with timing."""
    start = time.time()
    response = await call_next(request)
    elapsed = round((time.time() - start) * 1000, 1)

    logger.info(
        f"{request.method} {request.url.path} -> {response.status_code} ({elapsed}ms)"
    )
    return response


# ── Global Exception Handler ──────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled error on {request.url.path}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Please try again."},
    )


# ── Include Routers ───────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(query.router)
app.include_router(feedback.router)
app.include_router(health.router)


# ── Root ───────────────────────────────────────────────────────────
@app.get("/", tags=["System"])
async def root():
    return {
        "service": "Proviso",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }
