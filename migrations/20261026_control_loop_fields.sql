-- 控制闭环 v1：命令表补契约字段（2026-10-26 夜间，未在生产库执行）
-- 只在开发副本/测试库生效；生产库执行需人工确认并走变更窗口。
ALTER TABLE actuator_commands ADD COLUMN IF NOT EXISTS boot_id VARCHAR(36);
ALTER TABLE actuator_commands ADD COLUMN IF NOT EXISTS session_id VARCHAR(64);
ALTER TABLE actuator_commands ADD COLUMN IF NOT EXISTS command_seq INTEGER;
ALTER TABLE actuator_commands ADD COLUMN IF NOT EXISTS publish_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE actuator_commands ADD COLUMN IF NOT EXISTS last_publish_at TIMESTAMPTZ;
ALTER TABLE actuator_commands ADD COLUMN IF NOT EXISTS result_boot_id VARCHAR(36);
CREATE INDEX IF NOT EXISTS ix_actuator_commands_boot_id ON actuator_commands (boot_id);
