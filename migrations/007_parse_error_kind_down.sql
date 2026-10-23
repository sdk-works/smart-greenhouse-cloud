-- ============================================================
-- 007 回滚：只删 007 自己加的东西
--   * 删 parse_error_stats 表（007 新建）
--   * 删 raw_messages.parse_error_kind 列与索引
-- ============================================================
BEGIN;

DROP INDEX IF EXISTS ix_raw_parse_error_kind;
ALTER TABLE raw_messages DROP COLUMN IF EXISTS parse_error_kind;
DROP TABLE IF EXISTS parse_error_stats;

COMMIT;
