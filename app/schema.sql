-- ---------------------------------------------------------------------------
-- Patient registration schema
--
-- Notes:
--  * Constraints are enforced in the database as well as in the application,
--    since the voice agent and the REST API are independent write paths.
--  * Deletes are soft (deleted_at). Every read filters on deleted_at IS NULL.
--  * call_logs holds one transcript per call, linked to the record it created.
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$ BEGIN
  CREATE TYPE sex_enum AS ENUM ('Male', 'Female', 'Other', 'Decline to Answer');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS patients (
  patient_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Required demographics -------------------------------------------------
  first_name              VARCHAR(50)  NOT NULL CHECK (char_length(trim(first_name)) BETWEEN 1 AND 50),
  last_name               VARCHAR(50)  NOT NULL CHECK (char_length(trim(last_name))  BETWEEN 1 AND 50),
  date_of_birth           DATE         NOT NULL CHECK (date_of_birth <= CURRENT_DATE),
  sex                     sex_enum     NOT NULL,
  phone_number            CHAR(10)     NOT NULL CHECK (phone_number ~ '^[2-9][0-9]{9}$'),
  address_line_1          VARCHAR(200) NOT NULL CHECK (char_length(trim(address_line_1)) > 0),
  city                    VARCHAR(100) NOT NULL CHECK (char_length(trim(city)) BETWEEN 1 AND 100),
  state                   CHAR(2)      NOT NULL CHECK (state ~ '^[A-Z]{2}$'),
  zip_code                VARCHAR(10)  NOT NULL CHECK (zip_code ~ '^[0-9]{5}(-[0-9]{4})?$'),

  -- Optional demographics -------------------------------------------------
  email                   VARCHAR(254)     CHECK (email IS NULL OR email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[A-Za-z]{2,}$'),
  address_line_2          VARCHAR(100),
  insurance_provider      VARCHAR(100),
  insurance_member_id     VARCHAR(50)      CHECK (insurance_member_id IS NULL OR insurance_member_id ~ '^[A-Za-z0-9-]+$'),
  preferred_language      VARCHAR(50)      NOT NULL DEFAULT 'English',
  emergency_contact_name  VARCHAR(100),
  emergency_contact_phone CHAR(10)         CHECK (emergency_contact_phone IS NULL OR emergency_contact_phone ~ '^[2-9][0-9]{9}$'),

  -- Provenance / lifecycle -------------------------------------------------
  source                  VARCHAR(20)  NOT NULL DEFAULT 'api',   -- 'voice' | 'api' | 'seed'
  call_id                 VARCHAR(100),                          -- Vapi call id, for idempotency
  created_at              TIMESTAMPTZ  NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ  NOT NULL DEFAULT now(),
  deleted_at              TIMESTAMPTZ
);

-- Query params supported by GET /patients
CREATE INDEX IF NOT EXISTS idx_patients_last_name     ON patients (lower(last_name)) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_patients_dob           ON patients (date_of_birth)    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_patients_phone         ON patients (phone_number)     WHERE deleted_at IS NULL;

-- One patient row per call, so a repeated tool call cannot insert twice.
CREATE UNIQUE INDEX IF NOT EXISTS idx_patients_call_id ON patients (call_id) WHERE call_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS call_logs (
  call_log_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  call_id      VARCHAR(100) UNIQUE,
  patient_id   UUID REFERENCES patients(patient_id) ON DELETE SET NULL,
  caller_number VARCHAR(20),
  ended_reason VARCHAR(100),
  duration_seconds INTEGER,
  transcript   TEXT,
  summary      TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- updated_at maintenance -----------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_patients_updated_at ON patients;
CREATE TRIGGER trg_patients_updated_at
  BEFORE UPDATE ON patients
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
