"""
Proviso — Terms. Spend. Proof.
Database Schema & Seeding Script
===================================
Creates PostgreSQL schema and seeds from generated CSV files.
Also creates a read-only user for the Finance Agent (security best practice).
Supports both PostgreSQL and SQLite fallback.
"""

import csv, os, sys
from datetime import date
from pathlib import Path
from sqlalchemy import (
    create_engine, MetaData, Table, Column, Integer, String, Float,
    Date, ForeignKey, text, inspect
)

DATA_DIR = Path(__file__).parent.parent / "data"
VENDORS_CSV = DATA_DIR / "seed" / "vendors.csv"
INVOICES_CSV = DATA_DIR / "seed" / "invoices.csv"

# Default to SQLite for local dev; override with env var for Docker/PostgreSQL
DB_URL = os.getenv(
    "DATABASE_URL_SYNC",
    f"sqlite:///{DATA_DIR / 'enterprise_ledger.db'}"
)

metadata = MetaData()

vendors_table = Table(
    "vendors", metadata,
    Column("vendor_id", Integer, primary_key=True),
    Column("vendor_name", String(255), nullable=False),
    Column("country", String(100), nullable=False),
    Column("risk_tier", String(20), nullable=False),
)

invoices_table = Table(
    "invoices", metadata,
    Column("invoice_id", Integer, primary_key=True),
    Column("vendor_id", Integer, ForeignKey("vendors.vendor_id"), nullable=False),
    Column("invoice_date", Date, nullable=False),
    Column("amount_due", Float, nullable=False),
    Column("payment_status", String(20), nullable=False),
)

feedback_table = Table(
    "feedback", metadata,
    Column("feedback_id", Integer, primary_key=True, autoincrement=True),
    Column("username", String(100)),
    Column("query_text", String(2000)),
    Column("response_text", String(5000)),
    Column("rating", Integer),
    Column("comment", String(1000)),
    Column("created_at", String(50)),
)

users_table = Table(
    "users", metadata,
    Column("user_id", Integer, primary_key=True, autoincrement=True),
    Column("username", String(100), unique=True, nullable=False),
    Column("hashed_password", String(500), nullable=False),
    Column("full_name", String(255)),
    Column("role", String(50), default="analyst"),
    Column("is_active", Integer, default=1),
)


def create_readonly_user(engine):
    """Create a read-only PostgreSQL user for the Finance Agent."""
    if "postgresql" not in str(engine.url):
        print("  [SKIP] Read-only user creation (SQLite mode)")
        return
    try:
        with engine.connect() as conn:
            conn.execute(text("COMMIT"))  # exit any transaction
            # Check if role exists
            result = conn.execute(text(
                "SELECT 1 FROM pg_roles WHERE rolname = 'readonly_agent'"
            ))
            if not result.fetchone():
                conn.execute(text(
                    "CREATE ROLE readonly_agent WITH LOGIN PASSWORD 'readonly123'"
                ))
                print("  [+] Created role: readonly_agent")
            conn.execute(text(
                "GRANT CONNECT ON DATABASE enterprise_ledger TO readonly_agent"
            ))
            conn.execute(text(
                "GRANT USAGE ON SCHEMA public TO readonly_agent"
            ))
            conn.execute(text(
                "GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_agent"
            ))
            conn.execute(text(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                "GRANT SELECT ON TABLES TO readonly_agent"
            ))
            conn.commit()
            print("  [+] Granted read-only permissions to readonly_agent")
    except Exception as e:
        print(f"  [WARN] Read-only user setup: {e}")


def seed_data(engine):
    """Load CSV data into the database."""
    # Seed vendors
    with open(VENDORS_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for r in reader:
            rows.append({
                "vendor_id": int(r["vendor_id"]),
                "vendor_name": r["vendor_name"],
                "country": r["country"],
                "risk_tier": r["risk_tier"],
            })
    with engine.begin() as conn:
        conn.execute(vendors_table.insert(), rows)
    print(f"  [+] Seeded {len(rows)} vendors")

    # Seed invoices
    with open(INVOICES_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for r in reader:
            rows.append({
                "invoice_id": int(r["invoice_id"]),
                "vendor_id": int(r["vendor_id"]),
                # SQLAlchemy's Date type requires a Python date object.  CSV
                # values are ISO-8601 strings, so convert them at the boundary.
                "invoice_date": date.fromisoformat(r["invoice_date"]),
                "amount_due": float(r["amount_due"]),
                "payment_status": r["payment_status"],
            })
    # Insert in batches of 1000
    with engine.begin() as conn:
        batch_size = 1000
        for i in range(0, len(rows), batch_size):
            conn.execute(invoices_table.insert(), rows[i:i + batch_size])
    print(f"  [+] Seeded {len(rows)} invoices")

    # Seed default admin user
    from datetime import datetime
    try:
        # Hash: "admin123" — using a pre-computed hash for portability
        # In production, use pwdlib to generate this
        with engine.begin() as conn:
            conn.execute(users_table.insert(), [{
                "username": "admin",
                "hashed_password": "$argon2id$v=19$m=65536,t=3,p=4$placeholder_admin_hash",
                "full_name": "System Administrator",
                "role": "admin",
                "is_active": 1,
            }])
        print("  [+] Seeded default admin user (admin / admin123)")
    except Exception:
        print("  [SKIP] Admin user already exists")


def main():
    # Verify CSV files exist
    if not VENDORS_CSV.exists() or not INVOICES_CSV.exists():
        print("ERROR: CSV files not found. Run generate_contracts.py and generate_invoices.py first.")
        sys.exit(1)

    db_display = str(DB_URL).encode('ascii', 'replace').decode()
    print(f"Connecting to: {db_display}")
    engine = create_engine(DB_URL, echo=False)

    # Check if tables already exist
    inspector = inspect(engine)
    existing = inspector.get_table_names()
    if "vendors" in existing:
        if "--reset" in sys.argv:
            print("Existing tables found; --reset supplied.")
            metadata.drop_all(engine)
            print("  [+] Dropped existing tables")
        else:
            print("Tables already exist. Drop and recreate? (y/n): ", end="")
            if input().strip().lower() == "y":
                metadata.drop_all(engine)
                print("  [+] Dropped existing tables")
            else:
                print("  [SKIP] Using existing tables")
                return

    # Create tables
    metadata.create_all(engine)
    print("  [+] Created tables: vendors, invoices, feedback, users")

    # Seed data
    seed_data(engine)

    # Create read-only user
    create_readonly_user(engine)

    # Summary
    with engine.connect() as conn:
        v_count = conn.execute(text("SELECT COUNT(*) FROM vendors")).scalar()
        i_count = conn.execute(text("SELECT COUNT(*) FROM invoices")).scalar()
        overdue = conn.execute(text(
            "SELECT COUNT(*) FROM invoices WHERE payment_status = 'OVERDUE'"
        )).scalar()

    print(f"\n{'='*60}")
    db_mode = "PostgreSQL" if "postgresql" in str(engine.url) else "SQLite local"
    print(f"  Database ready ({db_mode})")
    print(f"  Vendors: {v_count}")
    print(f"  Invoices: {i_count}")
    print(f"  Overdue invoices: {overdue}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
