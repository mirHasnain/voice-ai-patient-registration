"""Applies app/schema.sql.

Safe to run repeatedly (every statement is IF NOT EXISTS / OR REPLACE).

    python scripts/migrate.py
"""

import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

import os  # noqa: E402

schema = (Path(__file__).resolve().parent.parent / "app" / "schema.sql").read_text(encoding="utf8")

url = os.environ.get("DATABASE_URL")
if not url:
    sys.exit("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")

try:
    with psycopg.connect(url, autocommit=True) as conn:
        # No placeholders in the script, so psycopg sends it over the simple
        # query protocol, which accepts multiple statements in one round trip.
        conn.execute(schema)
        row = conn.execute(
            "SELECT count(*)::int AS columns FROM information_schema.columns "
            "WHERE table_name = 'patients'"
        ).fetchone()
    print(f"Migration complete. patients table has {row[0]} columns.")
except Exception as exc:
    sys.exit(f"Migration failed: {exc}")
