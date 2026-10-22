-- ============================================================
-- 2026-10-22 第六批迁移（T3 设备入网与凭据管理闭环）
-- 性质：纯增量、幂等（可重复执行）
-- 目标库：greenhouse_nightly_test（**只允许在测试库执行，严禁生产库**）
--
-- 【重要】生产库里 mqtt_credentials 已存在（2026-10-22 手工建表）：
--   username(PK) / password_hash / clientid(UNIQUE) / device_id / enabled / note / created_at
--   username 与 clientid 的唯一约束已经建立了 username↔clientid 的一对一绑定。
--   因此本迁移用 CREATE TABLE IF NOT EXISTS + ADD COLUMN IF NOT EXISTS 写成幂等，
--   down 脚本**只删自己加的列**，绝不 DROP TABLE（否则会把生产已有凭据一行删掉）。
-- ============================================================
BEGIN;

CREATE TABLE IF NOT EXISTS mqtt_credentials (
  username        VARCHAR(100) PRIMARY KEY,
  password_hash   TEXT         NOT NULL,
  clientid        VARCHAR(100) NOT NULL UNIQUE,
  device_id       VARCHAR(100) NOT NULL,
  enabled         BOOLEAN      NOT NULL DEFAULT true,
  note            TEXT,
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- ── 006 新增列 ──────────────────────────────────────────────
ALTER TABLE mqtt_credentials ADD COLUMN IF NOT EXISTS asset_no          VARCHAR(64);
ALTER TABLE mqtt_credentials ADD COLUMN IF NOT EXISTS compat_identity   VARCHAR(100);
ALTER TABLE mqtt_credentials ADD COLUMN IF NOT EXISTS updated_at        TIMESTAMPTZ;
ALTER TABLE mqtt_credentials ADD COLUMN IF NOT EXISTS disabled_at       TIMESTAMPTZ;
ALTER TABLE mqtt_credentials ADD COLUMN IF NOT EXISTS last_rotated_at   TIMESTAMPTZ;
ALTER TABLE mqtt_credentials ADD COLUMN IF NOT EXISTS rotation_count    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE mqtt_credentials ADD COLUMN IF NOT EXISTS created_by        VARCHAR(36);
ALTER TABLE mqtt_credentials ADD COLUMN IF NOT EXISTS updated_by        VARCHAR(36);

-- 一个设备最多一套有效凭据（重试/幂等用 ON CONFLICT (device_id)）
CREATE UNIQUE INDEX IF NOT EXISTS ux_mqtt_credentials_device
  ON mqtt_credentials(device_id);
CREATE INDEX IF NOT EXISTS ix_mqtt_credentials_enabled
  ON mqtt_credentials(enabled);

COMMIT;
