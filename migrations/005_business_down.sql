-- ============================================================
-- 回滚：2026-10-21 第四批迁移（M4 业务模块）
-- 警告：会删除新增表与其中的业务数据。**只在测试库执行。**
-- ============================================================
BEGIN;

DROP TABLE IF EXISTS ai_advice_logs;
DROP TABLE IF EXISTS metric_definitions;
DROP TABLE IF EXISTS planting_batches;

COMMIT;
