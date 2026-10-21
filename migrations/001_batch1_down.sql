-- ============================================================
-- 回滚：2026-10-21 第一批迁移
-- 注意：会删除新增列/表及其中的业务数据（先备份）
-- ============================================================
BEGIN;

ALTER TABLE telemetry DROP COLUMN IF EXISTS raw_message_id;
ALTER TABLE telemetry DROP COLUMN IF EXISTS map_version_id;
ALTER TABLE telemetry DROP COLUMN IF EXISTS attribution_status;
ALTER TABLE telemetry DROP COLUMN IF EXISTS position_quality;
ALTER TABLE telemetry DROP COLUMN IF EXISTS company_id;

ALTER TABLE robot_point_configs DROP COLUMN IF EXISTS robot_map_version;
ALTER TABLE robot_point_configs DROP COLUMN IF EXISTS point_id;

ALTER TABLE users DROP COLUMN IF EXISTS is_platform_operator;
ALTER TABLE users DROP COLUMN IF EXISTS company_id;

ALTER TABLE greenhouses DROP COLUMN IF EXISTS schema_version;
ALTER TABLE greenhouses DROP COLUMN IF EXISTS coord_unit;
ALTER TABLE greenhouses DROP COLUMN IF EXISTS base_id;
ALTER TABLE greenhouses DROP COLUMN IF EXISTS company_id;

DROP TABLE IF EXISTS robot_point_mappings;
DROP TABLE IF EXISTS device_bindings;
DROP TABLE IF EXISTS map_versions;
DROP TABLE IF EXISTS raw_messages;
DROP TABLE IF EXISTS inspection_points;
DROP TABLE IF EXISTS bases;
DROP TABLE IF EXISTS companies;

COMMIT;
