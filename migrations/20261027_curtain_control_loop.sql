-- 控制契约 v1 rev.2：卷帘机云端闭环字段（2026-10-27，仅在开发副本/测试库生效）
-- 生产库执行需人工确认并走变更窗口；本迁移不在本轮部署。

-- 命令表：信封审计字段（契约 §5.1 / §8.5 / §11.4）
ALTER TABLE actuator_commands ADD COLUMN IF NOT EXISTS channel_id VARCHAR(16);
ALTER TABLE actuator_commands ADD COLUMN IF NOT EXISTS ttl_ms INTEGER;
ALTER TABLE actuator_commands ADD COLUMN IF NOT EXISTS ref_uptime_ms BIGINT;
ALTER TABLE actuator_commands ADD COLUMN IF NOT EXISTS result_session_id VARCHAR(64);

-- 设备控制状态：device_state 摄取目标（契约 §8.1 / §11.4 actuator_profiles 扩展）
ALTER TABLE actuator_profiles ADD COLUMN IF NOT EXISTS boot_id INTEGER;
ALTER TABLE actuator_profiles ADD COLUMN IF NOT EXISTS session_id VARCHAR(12);
ALTER TABLE actuator_profiles ADD COLUMN IF NOT EXISTS state_seq BIGINT;
ALTER TABLE actuator_profiles ADD COLUMN IF NOT EXISTS last_command_seq BIGINT;
ALTER TABLE actuator_profiles ADD COLUMN IF NOT EXISTS mode VARCHAR(16);
ALTER TABLE actuator_profiles ADD COLUMN IF NOT EXISTS motion VARCHAR(16);
ALTER TABLE actuator_profiles ADD COLUMN IF NOT EXISTS fault VARCHAR(24);
ALTER TABLE actuator_profiles ADD COLUMN IF NOT EXISTS limit_up BOOLEAN;
ALTER TABLE actuator_profiles ADD COLUMN IF NOT EXISTS limit_down BOOLEAN;
ALTER TABLE actuator_profiles ADD COLUMN IF NOT EXISTS estop BOOLEAN;
ALTER TABLE actuator_profiles ADD COLUMN IF NOT EXISTS position_raw BIGINT;
ALTER TABLE actuator_profiles ADD COLUMN IF NOT EXISTS position_valid BOOLEAN;
ALTER TABLE actuator_profiles ADD COLUMN IF NOT EXISTS uptime_ms BIGINT;
ALTER TABLE actuator_profiles ADD COLUMN IF NOT EXISTS state_received_at TIMESTAMPTZ;
