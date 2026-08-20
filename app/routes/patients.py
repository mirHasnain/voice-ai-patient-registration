"""REST API for patient records.

    GET    /patients          list, optional ?last_name= ?date_of_birth= ?phone_number=
    GET    /patients/{id}     fetch one by UUID
    POST   /patients          create
    PUT    /patients/{id}     partial update
    DELETE /patients/{id}     soft delete

All validation is server-side (validators.py). No client, including the voice
agent, is trusted to have validated its input.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from .. import service
from ..http import fail, json_body, ok
from ..validators import normalize_dob, normalize_phone, validate_patient

log = logging.getLogger("api")
router = APIRouter(prefix="/patients", tags=["patients"])

MAX_LIMIT = 200


@router.get("")
async def list_patients(request: Request):
    params = request.query_params

    # Filters are normalized the same way stored values are, so
    # ?phone_number=(555) 123-4567 and ?phone_number=5551234567 both match.
    dob = None
    if params.get("date_of_birth"):
        dob = normalize_dob(params["date_of_birth"])
        if not dob:
            return fail(400, "date_of_birth filter must be a valid date (MM/DD/YYYY or YYYY-MM-DD).")

    phone = None
    if params.get("phone_number"):
        phone = normalize_phone(params["phone_number"])
        if not phone:
            return fail(400, "phone_number filter must be a valid 10-digit US phone number.")

    def as_int(name, default):
        try:
            return int(params.get(name, default))
        except (TypeError, ValueError):
            return default

    limit = min(as_int("limit", 50), MAX_LIMIT)
    offset = max(as_int("offset", 0), 0)
    last_name = params.get("last_name")

    patients = service.list_patients(
        last_name=last_name.strip() if last_name else None,
        date_of_birth=dob,
        phone_number=phone,
        limit=limit,
        offset=offset,
    )
    return ok({"patients": patients, "count": len(patients), "limit": limit, "offset": offset})


@router.get("/{patient_id}")
async def get_patient(patient_id: str):
    if not service.is_uuid(patient_id):
        return fail(400, "patient_id must be a UUID.")
    patient = service.get_patient(patient_id)
    if not patient:
        return fail(404, "Patient not found.")
    return ok(patient)


@router.post("")
async def create_patient(request: Request):
    body = await json_body(request)
    result = validate_patient(body)
    if not result.ok:
        return fail(422, "Validation failed.", result.errors)

    patient = service.create_patient(result.value, source="api")
    log.info("patient created %s", patient["patient_id"])
    return ok(patient, 201)


@router.put("/{patient_id}")
async def update_patient(patient_id: str, request: Request):
    if not service.is_uuid(patient_id):
        return fail(400, "patient_id must be a UUID.")

    body = await json_body(request)
    result = validate_patient(body, partial=True)
    if not result.ok:
        return fail(422, "Validation failed.", result.errors)
    if not result.value:
        return fail(400, "No updatable fields supplied.")

    patient = service.update_patient(patient_id, result.value)
    if not patient:
        return fail(404, "Patient not found.")
    log.info("patient updated %s", patient["patient_id"])
    return ok(patient)


@router.delete("/{patient_id}")
async def delete_patient(patient_id: str):
    if not service.is_uuid(patient_id):
        return fail(400, "patient_id must be a UUID.")
    row = service.soft_delete_patient(patient_id)
    if not row:
        return fail(404, "Patient not found.")
    log.info("patient soft-deleted %s", row["patient_id"])
    return ok(row)
