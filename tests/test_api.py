"""Integration tests: real FastAPI app, real database.

Requires DATABASE_URL (loaded from .env by tests/conftest.py). Records created
here use a reserved 999-555-01xx phone range and are hard-deleted in the
teardown fixture so the demo data stays clean.
"""

import json
import os

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app

TEST_PHONE = "9995550101"
SECRET = os.environ.get("VAPI_WEBHOOK_SECRET")

NEW_PATIENT = {
    "first_name": "Testy", "last_name": "Mctest", "date_of_birth": "05/09/1992",
    "sex": "Other", "phone_number": TEST_PHONE, "address_line_1": "1 Test Way",
    "city": "Testville", "state": "TX", "zip_code": "73301",
}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client
    # The LIKE pattern is passed as a parameter: a literal % inside the SQL
    # string would be read by psycopg as a placeholder.
    db.query("DELETE FROM call_logs WHERE call_id LIKE %s", ("test-%",))
    db.query("DELETE FROM patients WHERE phone_number = %s", (TEST_PHONE,))
    db.close_pool()


def test_health_reports_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["status"] == "ok"
    assert body["error"] is None


def test_create_returns_201(client):
    response = client.post("/patients", json=NEW_PATIENT)
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["patient_id"]
    assert data["date_of_birth"] == "05/09/1992", "date is not shifted by timezone"
    assert response.json()["error"] is None


def test_create_rejects_invalid_input_with_422_and_field_details(client):
    response = client.post("/patients", json={
        **NEW_PATIENT, "date_of_birth": "01/01/2099", "phone_number": "12",
    })
    assert response.status_code == 422
    body = response.json()
    assert body["data"] is None
    fields = [d["field"] for d in body["error"]["details"]]
    assert "date_of_birth" in fields
    assert "phone_number" in fields


def test_malformed_json_is_400(client):
    response = client.post("/patients", content="{nope",
                           headers={"content-type": "application/json"})
    assert response.status_code == 400


def test_filters_by_last_name_phone_and_dob(client):
    by_name = client.get("/patients", params={"last_name": "mctest"})
    assert by_name.status_code == 200
    assert by_name.json()["data"]["count"] >= 1, "last_name filter is case-insensitive"

    by_phone = client.get("/patients", params={"phone_number": "(999) 555-0101"})
    assert by_phone.json()["data"]["count"] >= 1, "phone filter normalizes formatting"

    by_dob = client.get("/patients", params={"date_of_birth": "05/09/1992"})
    assert by_dob.json()["data"]["count"] >= 1


def test_unparseable_filter_is_400(client):
    assert client.get("/patients", params={"date_of_birth": "whenever"}).status_code == 400


def test_get_one_bad_uuid_400_unknown_404(client):
    assert client.get("/patients/nope").status_code == 400
    assert client.get("/patients/6f1c8f2e-0000-4000-8000-000000000000").status_code == 404


def test_partial_update_leaves_other_fields_alone(client):
    listing = client.get("/patients", params={"phone_number": TEST_PHONE})
    patient_id = listing.json()["data"]["patients"][0]["patient_id"]

    response = client.put(f"/patients/{patient_id}", json={"city": "Austin"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["city"] == "Austin"
    assert data["first_name"] == "Testy", "untouched fields survive"
    assert data["state"] == "TX"


def test_delete_is_soft(client):
    listing = client.get("/patients", params={"phone_number": TEST_PHONE})
    patient_id = listing.json()["data"]["patients"][0]["patient_id"]

    assert client.delete(f"/patients/{patient_id}").status_code == 200
    assert client.get(f"/patients/{patient_id}").status_code == 404, "no longer readable"

    rows = db.query("SELECT deleted_at FROM patients WHERE patient_id = %s", (patient_id,))
    assert len(rows) == 1, "row still exists"
    assert rows[0]["deleted_at"], "deleted_at is stamped"

    assert client.delete(f"/patients/{patient_id}").status_code == 404, "second delete is 404"


def test_unknown_route_uses_the_standard_envelope(client):
    response = client.get("/nope")
    assert response.status_code == 404
    body = response.json()
    assert body["data"] is None
    assert body["error"]["message"]


# --- voice webhook -------------------------------------------------------- #

def _webhook(client, message, secret=SECRET):
    headers = {"x-vapi-secret": secret} if secret else {}
    return client.post("/webhook/vapi", json={"message": message}, headers=headers)


def _tool_call(name, args, call_id):
    return {
        "type": "tool-calls",
        "call": {"id": call_id, "customer": {"number": f"+1{TEST_PHONE}"}},
        "toolCallList": [{"id": "tc-1", "function": {"name": name, "arguments": args}}],
    }


@pytest.mark.skipif(not SECRET, reason="VAPI_WEBHOOK_SECRET not configured")
def test_webhook_rejects_wrong_secret(client):
    response = _webhook(client, _tool_call("lookup_patient", {}, "test-auth"), secret="wrong")
    assert response.status_code == 401


def test_save_patient_writes_and_confirms(client):
    response = _webhook(client, _tool_call("save_patient", NEW_PATIENT, "test-save-1"))
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["result"].startswith("SAVED")
    assert result["toolCallId"] == "tc-1"


def test_save_patient_is_idempotent_per_call_id(client):
    _webhook(client, _tool_call("save_patient", NEW_PATIENT, "test-save-1"))
    rows = db.query(
        "SELECT count(*)::int AS n FROM patients "
        "WHERE call_id = 'test-save-1' AND deleted_at IS NULL"
    )
    assert rows[0]["n"] == 1, "a duplicate tool call does not duplicate the patient"


def test_lookup_recognizes_returning_caller(client):
    response = _webhook(client, _tool_call("lookup_patient", {}, "test-lookup"))
    result = response.json()["results"][0]["result"]
    assert result.startswith("MATCH_FOUND")
    assert "Testy" in result


def test_lookup_reports_no_match_for_unknown_number(client):
    response = _webhook(client, {
        "type": "tool-calls",
        "call": {"id": "test-lookup-2", "customer": {"number": "+19995550999"}},
        "toolCallList": [{"id": "tc-1", "function": {"name": "lookup_patient", "arguments": {}}}],
    })
    assert response.json()["results"][0]["result"].startswith("NO_MATCH")


def test_invalid_data_returns_reprompt_not_an_error_page(client):
    response = _webhook(client, _tool_call(
        "save_patient", {**NEW_PATIENT, "date_of_birth": "01/01/2099"}, "test-bad"))
    assert response.status_code == 200, "the call must not break on bad data"
    result = response.json()["results"][0]["result"]
    assert result.startswith("VALIDATION_FAILED")
    assert "date_of_birth" in result
    assert "Ask the caller" in result


def test_tool_arguments_sent_as_json_string(client):
    response = _webhook(client, _tool_call(
        "save_patient", json.dumps(NEW_PATIENT), "test-string-args"))
    assert response.json()["results"][0]["result"].startswith("SAVED")
    db.query("DELETE FROM patients WHERE call_id = 'test-string-args'")


def test_unknown_tool_name_does_not_break_the_call(client):
    response = _webhook(client, _tool_call("book_a_flight", {}, "test-unknown"))
    assert response.status_code == 200
    assert "Unknown tool" in response.json()["results"][0]["result"]


def test_end_of_call_report_persists_transcript(client):
    response = _webhook(client, {
        "type": "end-of-call-report",
        "call": {"id": "test-save-1", "customer": {"number": f"+1{TEST_PHONE}"}},
        "endedReason": "customer-ended-call",
        "durationSeconds": 42,
        "artifact": {"transcript": "AI: Hello. User: Hi."},
        "analysis": {"summary": "Registered a test patient."},
    })
    assert response.status_code == 200

    rows = db.query("SELECT transcript, duration_seconds FROM call_logs "
                    "WHERE call_id = 'test-save-1'")
    assert len(rows) == 1
    assert "User: Hi" in rows[0]["transcript"]
    assert rows[0]["duration_seconds"] == 42
