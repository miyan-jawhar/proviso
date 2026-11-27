"""
Application Configuration
==========================
Pydantic BaseSettings for type-safe, environment-driven configuration.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache
from pathlib import Path


_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
# In Docker the backend itself is mounted at /app and the data volume at
# /app/data; locally both live under the repository root.
PROJECT_ROOT = (
    _REPOSITORY_ROOT
    if (_REPOSITORY_ROOT / "data").exists()
    else _PACKAGE_ROOT
)
DATA_DIR = PROJECT_ROOT / "data"
_SQLITE_DB_PATH = (DATA_DIR / "enterprise_ledger.db").as_posix()


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ── Application ────────────────────────────────────────────────
    app_name: str = "Proviso"
    app_version: str = "1.0.0"
    # Avoid consuming a generic machine-level DEBUG variable (for example,
    # DEBUG=release in a developer shell).  Only the application-specific
    # PROVISO_DEBUG setting controls FastAPI/SQLAlchemy debug behavior.
    debug: bool = Field(default=False, validation_alias="PROVISO_DEBUG")

    # ── LLM Provider ───────────────────────────────────────────────
    llm_provider: str = Field(
        default="gemini",
        description="LLM provider: gemini, openai, or ollama",
    )
    gemini_api_key: str = Field(default="", description="Gemini Developer API key")
    gemini_model: str = Field(
        default="gemini-3-flash-preview",
        description="Gemini model name",
    )
    gemini_fallback_model: str = Field(
        default="gemini-3.5-flash",
        description="Fallback Gemini model for temporary provider overloads",
    )
    openai_api_key: str = Field(default="", description="OpenAI API key")
    openai_model: str = Field(default="gpt-4o", description="OpenAI model name")
    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="llama3.1:8b")

    # ── Database ───────────────────────────────────────────────────
    database_url: str = Field(
        default=f"sqlite+aiosqlite:///{_SQLITE_DB_PATH}",
        description="Async database URL"
    )
    database_url_sync: str = Field(
        default=f"sqlite:///{_SQLITE_DB_PATH}",
        description="Sync database URL for agents"
    )
    database_readonly_url: str = Field(
        default=f"sqlite:///{_SQLITE_DB_PATH}",
        description="Read-only DB URL for Finance Agent"
    )

    # ── ChromaDB ───────────────────────────────────────────────────
    chroma_host: str = "localhost"
    chroma_port: int = 8100
    chroma_collection: str = "vendor_contracts"
    use_local_chroma: bool = True
    local_chroma_dir: str = str(DATA_DIR / "chroma_db")

    # ── JWT Authentication ─────────────────────────────────────────
    jwt_secret_key: str = Field(
        default="change-this-in-production-use-a-long-random-string",
        description="JWT signing secret"
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # ── Rate Limiting ──────────────────────────────────────────────
    rate_limit_per_minute: int = 30

    # ── Observability ──────────────────────────────────────────────
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "proviso"

    # ── Embedding Model ────────────────────────────────────────────
    embedding_model: str = "sentence-transformers/all-mpnet-base-v2"
    embedding_cache_dir: str = str(PROJECT_ROOT / ".cache" / "huggingface")
    retrieval_min_relevance_score: float = Field(default=0.40, ge=0.0, le=1.0)

    model_config = {
        "env_file": PROJECT_ROOT / ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


@lru_cache()
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()
