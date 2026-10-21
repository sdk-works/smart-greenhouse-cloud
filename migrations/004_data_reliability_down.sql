-- ============================================================
-- 回滚：2026-10-21 第三批迁移（M3 数据可靠性）
-- 警告：会删除新增表与其全部数据。**只在测试库执行。**
-- ============================================================
BEGIN;

DROP TABLE IF EXISTS alerts;
DROP TABLE IF EXISTS incident_events;
DROP TABLE IF EXISTS incidents;

DROP INDEX IF EXISTS ix_device_bindings_device_start;
DROP INDEX IF EXISTS ix_device_bindings_channel;

ALTER TABLE telemetry DROP COLUMN IF EXISTS binding_version;
ALTER TABLE telemetry DROP COLUMN IF EXISTS binding_id;
ALTER TABLE telemetry DROP COLUMN IF EXISTS attribution_reason;

COMMIT;
