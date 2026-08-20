"""Two demo patients so the dashboard and the API are not empty on first look.

Idempotent: re-running does not create duplicates.

    python scripts/seed.py
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from app import service  # noqa: E402
from app.validators import validate_patient  # noqa: E402

SEEDS = [
    {
        "first_name": "Jane", "last_name": "Doe", "date_of_birth": "04/17/1985",
        "sex": "Female", "phone_number": "2125550143", "email": "jane.doe@example.com",
        "address_line_1": "221 Baker Street", "address_line_2": "Apt 4B",
        "city": "New York", "state": "NY", "zip_code": "10014",
        "insurance_provider": "Blue Cross Blue Shield",
        "insurance_member_id": "BCBS4471902", "preferred_language": "English",
        "emergency_contact_name": "John Doe", "emergency_contact_phone": "2125550188",
    },
    {
        "first_name": "Carlos", "last_name": "Ramirez", "date_of_birth": "11/02/1971",
        "sex": "Male", "phone_number": "3055550117",
        "address_line_1": "840 Ocean Drive", "city": "Miami", "state": "FL",
        "zip_code": "33139", "preferred_language": "Spanish",
    },
]

exit_code = 0
for seed in SEEDS:
    existing = service.find_by_phone(seed["phone_number"])
    if existing:
        print(f"skip  {seed['first_name']} {seed['last_name']} "
              f"(already present: {existing['patient_id']})")
        continue

    result = validate_patient(seed)
    if not result.ok:
        print(f"FAIL  {seed['first_name']}: {result.errors}")
        exit_code = 1
        continue

    patient = service.create_patient(result.value, source="seed")
    print(f"seed  {patient['first_name']} {patient['last_name']} -> {patient['patient_id']}")

sys.exit(exit_code)
