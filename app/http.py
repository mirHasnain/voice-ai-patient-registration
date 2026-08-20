"""Response envelope helpers.

Every endpoint returns {"data": ..., "error": ...} on both success and failure,
so clients do not have to branch on the response shape.
"""

from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import JSONResponse


def ok(data, status: int = 200) -> JSONResponse:
    return JSONResponse({"data": data, "error": None}, status_code=status)


def fail(status: int, message: str, details=None) -> JSONResponse:
    error = {"message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse({"data": None, "error": error}, status_code=status)


async def json_body(request: Request):
    """Parse a JSON request body.

    Read by hand so that unparseable input answers 400 while a well-formed body
    that fails validation answers 422. FastAPI's automatic binding returns 422
    for both.
    """
    raw = await request.body()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise MalformedJSON()
    if not isinstance(parsed, dict):
        raise MalformedJSON()
    return parsed


class MalformedJSON(Exception):
    """Raised when a request body is not a JSON object."""
