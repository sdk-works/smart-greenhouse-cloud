-- Add seven-parameter soil measurements and task traceability.
-- Safe to run repeatedly on PostgreSQL 14.
BEGIN;

ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS soil_temperature DOUBLE PRECISION;
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS soil_ec DOUBLE PRECISION;
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS soil_nitrogen DOUBLE PRECISION;
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS soil_phosphorus DOUBLE PRECISION;
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS soil_potassium DOUBLE PRECISION;
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS sample_count INTEGER;
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS task_id VARCHAR(100);
CREATE INDEX IF NOT EXISTS ix_telemetry_task_id ON telemetry (task_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_telemetry_msg_id
    ON telemetry (msg_id) WHERE msg_id IS NOT NULL;

COMMIT;
