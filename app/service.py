"""Data-access layer.

The only module that issues SQL. Both write paths, the REST API
(app/routes/patients.py) and the voice webhook (app/routes/vapi.py), go through
these functions, so records created by phone and by API are handled the same
way.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from uuid import UUID

from . import db

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def is_uuid(value) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.match(value))


def _shape(row: dict | None) -> dict | None:
    """Map a database row to its API representation.

    Internal columns such as deleted_at and call_id are not exposed, and
    date_of_birth is rendered as MM/DD/YYYY.
    """
    if not row:
        return None

    dob = row["date_of_birth"]
    dob = dob if isinstance(dob, date) else date.fromisoformat(str(dob)[:10])

    def stamp(value):
        return value.isoformat() if isinstance(value, datetime) else value

    return {
        "patient_id": str(row["patient_id"]),
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "date_of_birth": dob.strftime("%m/%d/%Y"),
        "sex": row["sex"],
        "phone_number": row["phone_number"],
        "email": row["email"],
        "address_line_1": row["address_line_1"],
        "address_line_2": row["address_line_2"],
        "city": row["city"],
        "state": row["state"],
        "zip_code": row["zip_code"],
        "insurance_provider": row["insurance_provider"],
        "insurance_member_id": row["insurance_member_id"],
        "preferred_language": row["preferred_language"],
        "emergency_contact_name": row["emergency_contact_name"],
        "emergency_contact_phone": row["emergency_contact_phone"],
        "source": row["source"],
        "created_at": stamp(row["created_at"]),
        "updated_at": stamp(row["updated_at"]),
    }


_INSERT = """
    INSERT INTO patients (
      first_name, last_name, date_of_birth, sex, phone_number, email,
      address_line_1, address_line_2, city, state, zip_code,
      insurance_provider, insurance_member_id, preferred_language,
      emergency_contact_name, emergency_contact_phone, source, call_id
    ) VALUES (
      %s, %s, %s, %s::sex_enum, %s, %s, %s, %s, %s, %s, %s,
      %s, %s, COALESCE(%s, 'English'), %s, %s, %s, %s
    )
    ON CONFLICT (call_id) WHERE call_id IS NOT NULL DO NOTHING
    RETURNING *
"""


def create_patient(data: dict, *, source: str = "api", call_id: str | None = None) -> dict | None:
    """Insert a patient.

    Idempotent per call_id. A model that repeats a tool call would otherwise
    create a duplicate record; the partial unique index makes the second insert
    a no-op and the existing row is returned.
    """
    params = (
        data["first_name"], data["last_name"], data["date_of_birth"], data["sex"],
        data["phone_number"], data.get("email"), data["address_line_1"],
        data.get("address_line_2"), data["city"], data["state"], data["zip_code"],
        data.get("insurance_provider"), data.get("insurance_member_id"),
        data.get("preferred_language"), data.get("emergency_contact_name"),
        data.get("emergency_contact_phone"), source, call_id,
    )
    row = db.query_one(_INSERT, params)
    if row:
        return _shape(row)

    existing = db.query_one("SELECT * FROM patients WHERE call_id = %s LIMIT 1", (call_id,))
    return _shape(existing)


def get_patient(patient_id: str) -> dict | None:
    row = db.query_one(
        "SELECT * FROM patients WHERE patient_id = %s AND deleted_at IS NULL LIMIT 1",
        (UUID(patient_id),),
    )
    return _shape(row)


def list_patients(*, last_name=None, date_of_birth=None, phone_number=None,
                  limit: int = 50, offset: int = 0) -> list[dict]:
    """List patients, with three optional filters.

    A single fixed statement with nullable parameters, so no user input is
    concatenated into the query.
    """
    rows = db.query(
        """
        SELECT * FROM patients
        WHERE deleted_at IS NULL
          AND (%s::text IS NULL OR lower(last_name) = lower(%s))
          AND (%s::date IS NULL OR date_of_birth = %s::date)
          AND (%s::text IS NULL OR phone_number = %s)
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
        """,
        (last_name, last_name, date_of_birth, date_of_birth,
         phone_number, phone_number, limit, offset),
    )
    return [_shape(r) for r in rows]


def count_patients() -> int:
    row = db.query_one("SELECT count(*)::int AS n FROM patients WHERE deleted_at IS NULL")
    return row["n"]


def find_by_phone(phone_number: str | None) -> dict | None:
    """Return the most recent active patient for a phone number."""
    if not phone_number:
        return None
    row = db.query_one(
        """
        SELECT * FROM patients
        WHERE phone_number = %s AND deleted_at IS NULL
        ORDER BY created_at DESC LIMIT 1
        """,
        (phone_number,),
    )
    return _shape(row)


_UPDATE = """
    UPDATE patients SET
      first_name              = COALESCE(%s, first_name),
      last_name               = COALESCE(%s, last_name),
      date_of_birth           = COALESCE(%s::date, date_of_birth),
      sex                     = COALESCE(%s::sex_enum, sex),
      phone_number            = COALESCE(%s, phone_number),
      email                   = COALESCE(%s, email),
      address_line_1          = COALESCE(%s, address_line_1),
      address_line_2          = COALESCE(%s, address_line_2),
      city                    = COALESCE(%s, city),
      state                   = COALESCE(%s, state),
      zip_code                = COALESCE(%s, zip_code),
      insurance_provider      = COALESCE(%s, insurance_provider),
      insurance_member_id     = COALESCE(%s, insurance_member_id),
      preferred_language      = COALESCE(%s, preferred_language),
      emergency_contact_name  = COALESCE(%s, emergency_contact_name),
      emergency_contact_phone = COALESCE(%s, emergency_contact_phone)
    WHERE patient_id = %s AND deleted_at IS NULL
    RETURNING *
"""

_UPDATABLE = [
    "first_name", "last_name", "date_of_birth", "sex", "phone_number", "email",
    "address_line_1", "address_line_2", "city", "state", "zip_code",
    "insurance_provider", "insurance_member_id", "preferred_language",
    "emergency_contact_name", "emergency_contact_phone",
]


def update_patient(patient_id: str, changes: dict) -> dict | None:
    """Apply a partial update.

    COALESCE leaves a column unchanged when the field was not supplied. The
    trade-off is that an optional field cannot be cleared through PUT; see
    README, Known Limitations.
    """
    params = tuple(changes.get(field) for field in _UPDATABLE) + (UUID(patient_id),)
    return _shape(db.query_one(_UPDATE, params))


def soft_delete_patient(patient_id: str) -> dict | None:
    """Mark a patient deleted. The row is retained and excluded from reads."""
    row = db.query_one(
        """
        UPDATE patients SET deleted_at = now()
        WHERE patient_id = %s AND deleted_at IS NULL
        RETURNING patient_id, deleted_at
        """,
        (UUID(patient_id),),
    )
    if not row:
        return None
    return {"patient_id": str(row["patient_id"]), "deleted_at": row["deleted_at"].isoformat()}


def log_call(*, call_id, patient_id=None, caller_number=None, ended_reason=None,
             duration_seconds=None, transcript=None, summary=None) -> None:
    """Record a call transcript against the patient it produced."""
    db.query(
        """
        INSERT INTO call_logs (
          call_id, patient_id, caller_number, ended_reason,
          duration_seconds, transcript, summary
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (call_id) DO UPDATE SET
          patient_id       = COALESCE(EXCLUDED.patient_id, call_logs.patient_id),
          ended_reason     = COALESCE(EXCLUDED.ended_reason, call_logs.ended_reason),
          duration_seconds = COALESCE(EXCLUDED.duration_seconds, call_logs.duration_seconds),
          transcript       = COALESCE(EXCLUDED.transcript, call_logs.transcript),
          summary          = COALESCE(EXCLUDED.summary, call_logs.summary)
        """,
        (call_id, UUID(patient_id) if patient_id else None, caller_number,
         ended_reason, duration_seconds, transcript, summary),
    )


def list_calls(limit: int = 20) -> list[dict]:
    rows = db.query(
        """
        SELECT call_id, patient_id, caller_number, ended_reason,
               duration_seconds, summary, created_at
        FROM call_logs ORDER BY created_at DESC LIMIT %s
        """,
        (limit,),
    )
    for row in rows:
        row["patient_id"] = str(row["patient_id"]) if row["patient_id"] else None
        row["created_at"] = row["created_at"].isoformat()
    return rows
