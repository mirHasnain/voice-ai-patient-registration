"""Database connection pool.

The pool is created on first use rather than at import. A failure at import
time surfaces as a generic invocation error on serverless, whereas deferring it
allows /health to report the missing configuration.

Pool size is small because each instance serves one request at a time and the
database connection limit is shared across all warm instances.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None


def is_configured() -> bool:
    """Return True when DATABASE_URL is set, without opening a connection."""
    return bool(os.environ.get("DATABASE_URL"))


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is not None:
        return _pool

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it in Vercel under Settings -> "
            "Environment Variables (or copy .env.example to .env locally), "
            "then redeploy."
        )

    _pool = ConnectionPool(
        conninfo=url,
        min_size=0,          # no connection held open while idle
        max_size=2,
        open=True,
        timeout=10,
        kwargs={"row_factory": dict_row},
    )
    return _pool


def close_pool() -> None:
    """Close the pool.

    Used by tests. Leaving it to the garbage collector makes psycopg_pool join
    its worker threads during interpreter shutdown, which raises
    PythonFinalizationError on Python 3.14.
    """
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def cursor():
    """Yield a dict-returning cursor inside a transaction that commits on exit."""
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            yield cur


def query(sql: str, params: tuple = ()) -> list[dict]:
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


def query_one(sql: str, params: tuple = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None
