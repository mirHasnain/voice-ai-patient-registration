"""Unit tests for normalization + validation.

No database, no network - these cover the logic that decides whether a caller's
answer is usable.
"""

import pytest

from app.validators import (
    normalize_dob, normalize_email, normalize_name, normalize_phone,
    normalize_sex, normalize_state, normalize_zip, validate_patient,
)


@pytest.mark.parametrize("raw,expected", [
    ("d-a-v-i-s", "Davis"),
    ("D-A-V-I-S", "Davis"),
    ("smith-jones", "Smith-Jones"),
    ("o'brien", "O'Brien"),
    ("  maria   ", "Maria"),
])
def test_spell_out_joined_real_hyphens_kept(raw, expected):
    assert normalize_name(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("McDonald", "McDonald"),
    ("DeAngelo", "DeAngelo"),
    ("MARIA", "Maria"),
])
def test_deliberate_internal_capitals_survive(raw, expected):
    assert normalize_name(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("(415) 555-0192", "4155550192"),
    ("+1 415 555 0192", "4155550192"),
    ("four one five five five five zero one nine two", "4155550192"),
])
def test_phone_formats(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["555", "1155550192", "0155550192"])
def test_phone_rejects_impossible_numbers(raw):
    assert normalize_phone(raw) is None


@pytest.mark.parametrize("raw,expected", [
    ("03/03/1990", "1990-03-03"),
    ("1990-03-03", "1990-03-03"),
    ("March 3, 1990", "1990-03-03"),
    ("3rd of March, 1990", "1990-03-03"),
    ("July 14th, 1978", "1978-07-14"),
])
def test_dob_accepts_numeric_iso_and_spoken(raw, expected):
    assert normalize_dob(raw) == expected


@pytest.mark.parametrize("raw", ["02/31/1990", "13/01/1990", "not a date"])
def test_dob_rejects_impossible_dates(raw):
    assert normalize_dob(raw) is None


def test_state_zip_email_sex():
    assert normalize_state("California") == "CA"
    assert normalize_state("new york") == "NY"
    assert normalize_state("ca") == "CA"
    assert normalize_state("Atlantis") is None

    assert normalize_zip("94103") == "94103"
    assert normalize_zip("nine four one zero three") == "94103"
    assert normalize_zip("941031234") == "94103-1234"
    assert normalize_zip("941") is None

    assert normalize_email("john dot smith at gmail dot com") == "john.smith@gmail.com"
    assert normalize_sex("f") == "Female"
    assert normalize_sex("prefer not to say") == "Decline to Answer"
    assert normalize_sex("banana") is None


VALID = {
    "first_name": "Robert", "last_name": "Davis", "date_of_birth": "07/14/1978",
    "sex": "male", "phone_number": "(650) 555-1234",
    "address_line_1": "1600 Amphitheatre Pkwy", "city": "Mountain View",
    "state": "California", "zip_code": "94043",
}


def test_accepts_and_normalizes_a_good_record():
    result = validate_patient(VALID)
    assert result.ok
    assert result.value["state"] == "CA"
    assert result.value["phone_number"] == "6505551234"
    assert result.value["date_of_birth"] == "1978-07-14"
    assert result.value["sex"] == "Male"


def test_rejects_future_date_of_birth():
    result = validate_patient({**VALID, "date_of_birth": "01/01/2099"})
    assert not result.ok
    assert any(e["field"] == "date_of_birth" for e in result.errors)


def test_errors_carry_a_reprompt_instruction_for_the_agent():
    result = validate_patient({**VALID, "phone_number": "123"})
    assert not result.ok
    error = next(e for e in result.errors if e["field"] == "phone_number")
    assert "Ask the caller" in error["message"]


def test_every_missing_required_field_reported_at_once():
    result = validate_patient({"first_name": "Solo"})
    assert not result.ok
    assert len(result.errors) == 8, "the other eight required fields"


def test_partial_mode_validates_only_what_was_supplied():
    result = validate_patient({"city": "Oakland"}, partial=True)
    assert result.ok
    assert result.value == {"city": "Oakland"}


def test_partial_mode_still_rejects_invalid_supplied_value():
    assert not validate_patient({"state": "Atlantis"}, partial=True).ok
