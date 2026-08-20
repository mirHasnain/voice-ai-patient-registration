"""Vercel entry point.

Vercel's Python runtime serves the module-level `app` as an ASGI application.
The project root is put on sys.path explicitly so `app.*` resolves regardless of
the working directory the runtime starts in.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.main import app  # noqa: E402,F401
