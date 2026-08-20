"""Vapi webhook, connecting the voice agent to the data layer.

Two message types are handled:

    tool-calls            the model invoked one of the registered tools
    end-of-call-report    the call finished, with the transcript attached

Tool responses take the form
{"results": [{"toolCallId": ..., "result": ...}]}, where `result` is text the
model reads back to the caller rather than a status code. Failure paths
therefore return a spoken instruction, so a rejected write becomes a re-prompt
for the affected field instead of silence.
"""

from __future__ import annotations

import json
import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import service
from ..http import json_body
from ..validators import normalize_phone, validate_patient

log = logging.getLogger("vapi")
router = APIRouter(prefix="/webhook", tags=["voice"])


def _authorized(request: Request) -> bool:
    """Check the shared secret so the webhook cannot be driven by a third party.

    When the secret is unset the request is allowed and a warning is logged, so
    local development does not require configuration.
    """
    expected = os.environ.get("VAPI_WEBHOOK_SECRET")
    if not expected:
        log.warning("VAPI_WEBHOOK_SECRET is not set - webhook is unauthenticated")
        return True
    return request.headers.get("x-vapi-secret") == expected


def _parse_args(raw):
    """Arguments arrive as an object or as a JSON string depending on the model."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        log.error("could not parse tool arguments: %r", raw)
        return {}


def _extract_tool_calls(message: dict) -> list[dict]:
    """Extract tool calls from the payload.

    The field has changed across Vapi versions (functionCall, then toolCalls,
    then toolCallList). All three are read so a provider update does not break
    an in-progress call.
    """
    listing = message.get("toolCallList") or message.get("toolCalls") or message.get("tool_calls") or []
    if listing:
        calls = []
        for item in listing:
            fn = item.get("function") or {}
            calls.append({
                "id": item.get("id") or item.get("toolCallId"),
                "name": fn.get("name") or item.get("name"),
                "args": _parse_args(fn.get("arguments") or item.get("arguments") or fn.get("parameters")),
            })
        return calls

    legacy = message.get("functionCall")
    if legacy:
        return [{
            "id": legacy.get("id", "legacy"),
            "name": legacy.get("name"),
            "args": _parse_args(legacy.get("parameters") or legacy.get("arguments")),
        }]
    return []


def _caller_number(message: dict):
    """The number the caller is dialling from, used to prefill phone_number."""
    call = message.get("call") or {}
    return (
        (call.get("customer") or {}).get("number")
        or (message.get("customer") or {}).get("number")
        or call.get("from")
    )


def _speak_digits(value) -> str:
    return " ".join(str(value or ""))


# --------------------------------------------------------------------------- #
# Tool implementations                                                        #
# --------------------------------------------------------------------------- #

def _lookup_patient(args: dict, ctx: dict) -> str:
    """Look up an existing record by caller ID, for returning patients."""
    phone = normalize_phone(args.get("phone_number") or ctx["caller_number"])
    if not phone:
        return ("NO_MATCH. No usable phone number was available. "
                "Continue with a normal new-patient registration.")

    patient = service.find_by_phone(phone)
    if not patient:
        return (f"NO_MATCH. No existing record for that number. Proceed with a new "
                f"registration. You may use {_speak_digits(phone)} as their phone "
                f"number after confirming it with the caller.")

    return "\n".join([
        "MATCH_FOUND. An existing record belongs to this phone number:",
        f"patient_id: {patient['patient_id']}",
        f"name: {patient['first_name']} {patient['last_name']}",
        f"date_of_birth: {patient['date_of_birth']}",
        f"address: {patient['address_line_1']}, {patient['city']}, "
        f"{patient['state']} {patient['zip_code']}",
        "",
        f'Greet them by first name and ask: "It looks like we already have a record '
        f'for {patient["first_name"]} {patient["last_name"]}. Would you like to '
        f'update your information instead?"',
        "If they say yes, collect only the fields they want changed and call "
        "update_patient with this patient_id.",
        "If they say no, or they are a different person, continue with a brand new "
        "registration using save_patient.",
    ])


def _save_patient(args: dict, ctx: dict) -> str:
    """Create the record, after the caller has confirmed the readback."""
    payload = dict(args)
    if not payload.get("phone_number") and ctx["caller_number"]:
        payload["phone_number"] = ctx["caller_number"]

    result = validate_patient(payload)

    if not result.ok:
        # One instruction per rejected field, so the agent re-prompts for those
        # fields rather than restarting the interview.
        log.warning("save_patient rejected on call %s: %s",
                    ctx["call_id"], [e["field"] for e in result.errors])
        lines = [f"- {e['field']}: {e['message']}" for e in result.errors]
        return "\n".join([
            "VALIDATION_FAILED. The record was NOT saved. Problems:",
            *lines,
            "",
            "Apologise briefly, ask the caller only about the fields listed above, "
            "then call save_patient again with the full set of information.",
        ])

    try:
        patient = service.create_patient(result.value, source="voice", call_id=ctx["call_id"])

        # Log the collected payload.
        log.info("PATIENT REGISTERED %s", json.dumps({
            "call_id": ctx["call_id"],
            "patient_id": patient["patient_id"],
            "payload": result.value,
        }))

        if ctx["call_id"]:
            try:
                service.log_call(call_id=ctx["call_id"], patient_id=patient["patient_id"],
                                 caller_number=ctx["caller_number"])
            except Exception as exc:  # linking a transcript must never fail the call
                log.error("call log link failed: %s", exc)

        return (f"SAVED. Patient {patient['first_name']} {patient['last_name']} is "
                "registered. Tell the caller they are all set, thank them by first "
                "name, and end the call politely. Do not read the patient ID aloud.")

    except Exception as exc:
        log.exception("save_patient DB failure on call %s: %s", ctx["call_id"], exc)
        return ("SAVE_FAILED. The database could not be reached. Apologise to the "
                "caller, tell them their information could not be saved right now "
                "and that someone from the clinic will call them back shortly, then "
                "end the call politely. Do not retry more than once.")


def _update_patient(args: dict, ctx: dict) -> str:
    """Update fields on an existing record."""
    patient_id = args.get("patient_id")
    if not service.is_uuid(patient_id):
        return ("UPDATE_FAILED. No valid patient_id was supplied. Call "
                "lookup_patient first to obtain one.")

    result = validate_patient(args, partial=True)
    if not result.ok:
        lines = [f"- {e['field']}: {e['message']}" for e in result.errors]
        return "\n".join([
            "VALIDATION_FAILED. Nothing was changed. Problems:", *lines, "",
            "Ask the caller again about those fields only, then call update_patient again.",
        ])
    if not result.value:
        return ("UPDATE_FAILED. No fields to change were supplied. Ask the caller "
                "which details they want to update.")

    try:
        patient = service.update_patient(patient_id, result.value)
        if not patient:
            return ("UPDATE_FAILED. That record no longer exists. Offer to register "
                    "the caller as a new patient instead.")

        log.info("PATIENT UPDATED %s", json.dumps({
            "call_id": ctx["call_id"], "patient_id": patient["patient_id"],
            "changes": result.value,
        }))
        return (f"UPDATED. The record for {patient['first_name']} "
                f"{patient['last_name']} has been updated. Confirm the change back "
                "to the caller and ask if there is anything else.")
    except Exception as exc:
        log.exception("update_patient DB failure: %s", exc)
        return ("UPDATE_FAILED. The database could not be reached. Apologise and "
                "tell the caller someone will call them back shortly.")


TOOLS = {
    "lookup_patient": _lookup_patient,
    "save_patient": _save_patient,
    "update_patient": _update_patient,
}


# --------------------------------------------------------------------------- #
# Route                                                                       #
# --------------------------------------------------------------------------- #

@router.post("/vapi")
async def vapi_webhook(request: Request):
    if not _authorized(request):
        log.warning("rejected webhook with bad or missing x-vapi-secret")
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    body = await json_body(request)
    message = body.get("message") or body or {}
    ctx = {
        "call_id": (message.get("call") or {}).get("id"),
        "caller_number": _caller_number(message),
    }
    message_type = message.get("type")

    try:
        if message_type in ("tool-calls", "function-call", "tool_calls"):
            calls = _extract_tool_calls(message)
            if not calls:
                return JSONResponse({"results": []})

            results = []
            for call in calls:
                impl = TOOLS.get(call["name"])
                if not impl:
                    log.error("unknown tool requested: %s", call["name"])
                    results.append({
                        "toolCallId": call["id"],
                        "result": f'Unknown tool "{call["name"]}". Continue the '
                                  "conversation without it.",
                    })
                    continue

                log.info("tool call %s on call %s", call["name"], ctx["call_id"])
                try:
                    results.append({"toolCallId": call["id"], "result": impl(call["args"], ctx)})
                except Exception as exc:
                    log.exception("tool %s threw: %s", call["name"], exc)
                    results.append({
                        "toolCallId": call["id"],
                        "result": "An internal error occurred. Apologise to the caller, "
                                  "let them know someone will follow up, and end the "
                                  "call politely.",
                    })
            return JSONResponse({"results": results})

        if message_type == "end-of-call-report":
            _handle_end_of_call(message, ctx)
            return JSONResponse({"received": True})

        # status-update, speech-update, hang, transcript, etc.
        return JSONResponse({"received": True})

    except Exception as exc:
        log.exception("webhook error: %s", exc)
        # Answer 200 regardless: a 5xx causes Vapi to retry, and a retry during
        # a live call is worse than a dropped event. The error is logged above.
        return JSONResponse({"received": False}, status_code=200)


def _handle_end_of_call(message: dict, ctx: dict) -> None:
    """Store the transcript for a finished call.

    Also covers a dropped connection, where the conversation is retained even
    though no patient record was written.
    """
    artifact = message.get("artifact") or {}
    transcript = artifact.get("transcript") or message.get("transcript")
    summary = (message.get("analysis") or {}).get("summary") or message.get("summary")
    ended_reason = message.get("endedReason") or message.get("ended_reason")

    duration = message.get("durationSeconds")
    if duration is None and message.get("startedAt") and message.get("endedAt"):
        from datetime import datetime
        started = datetime.fromisoformat(message["startedAt"].replace("Z", "+00:00"))
        ended = datetime.fromisoformat(message["endedAt"].replace("Z", "+00:00"))
        duration = (ended - started).total_seconds()

    log.info("CALL ENDED %s", json.dumps({
        "call_id": ctx["call_id"], "ended_reason": ended_reason,
        "duration_seconds": duration,
    }))

    if not ctx["call_id"]:
        return

    service.log_call(
        call_id=ctx["call_id"],
        caller_number=ctx["caller_number"],
        ended_reason=ended_reason,
        duration_seconds=round(duration) if duration is not None else None,
        transcript=transcript if isinstance(transcript, str) else json.dumps(transcript),
        summary=summary,
    )
