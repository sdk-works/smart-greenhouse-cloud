-- ============================================================
-- 回滚：2026-10-22 第六批迁移（T3 设备入网与凭据管理闭环）
-- **只在测试库执行。**
--
-- 注意：**不 DROP TABLE mqtt_credentials**。生产库里这张表在 006 之前就存在，
-- 里面已有 dev-jy-dtu-000001 一行；down 脚本只负责剔除 006 自己加的列与索引。
-- ============================================================
BEGIN;

DROP INDEX IF EXISTS ix_mqtt_credentials_enabled;
DROP INDEX IF EXISTS ux_mqtt_credentials_device;

ALTER TABLE mqtt_credentials DROP COLUMN IF EXISTS updated_by;
ALTER TABLE mqtt_credentials DROP COLUMN IF EXISTS created_by;
ALTER TABLE mqtt_credentials DROP COLUMN IF EXISTS rotation_count;
ALTER TABLE mqtt_credentials DROP COLUMN IF EXISTS last_rotated_at;
ALTER TABLE mqtt_credentials DROP COLUMN IF EXISTS disabled_at;
ALTER TABLE mqtt_credentials DROP COLUMN IF EXISTS updated_at;
ALTER TABLE mqtt_credentials DROP COLUMN IF EXISTS compat_identity;
ALTER TABLE mqtt_credentials DROP COLUMN IF EXISTS asset_no;

-- 测试库想彻底推倒重来时，单独手执行：
--   DROP TABLE IF EXISTS mqtt_credentials;
COMMIT;
