-- ============================================================
-- 回滚：2026-10-21 第二批迁移（M2 机器人闭环）
-- 警告：会删除新增表与其全部数据。**只在测试库执行。**
-- 顺序：先删依赖表，再删列，最后把状态列改回枚举（默认不改回，见注释）
-- ============================================================
BEGIN;

DROP TABLE IF EXISTS robot_map_alerts;
DROP TABLE IF EXISTS robot_map_registry;
DROP TABLE IF EXISTS inspection_evidence;

ALTER TABLE images DROP COLUMN IF EXISTS company_id;
ALTER TABLE images DROP COLUMN IF EXISTS batch_id;
ALTER TABLE images DROP COLUMN IF EXISTS map_version_id;
ALTER TABLE images DROP COLUMN IF EXISTS point_id;

ALTER TABLE robot_tasks DROP CONSTRAINT IF EXISTS fk_robot_tasks_map_version_id;
ALTER TABLE robot_tasks DROP COLUMN IF EXISTS map_version_id;
ALTER TABLE robot_tasks DROP COLUMN IF EXISTS snapshot;

-- 状态列：默认保留 VARCHAR(20)。若确需恢复为原生枚举，取消下面注释：
-- （注意：恢复成枚举会丢失 'paused' / 'partial' 这些新状态对应的行）
-- CREATE TYPE taskstatus AS ENUM ('pending','running','completed','failed','cancelled');
-- ALTER TABLE robot_tasks
--   ALTER COLUMN status TYPE taskstatus USING status::taskstatus;

COMMIT;
