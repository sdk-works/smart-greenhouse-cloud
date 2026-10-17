BEGIN;

-- 固定终端(device 通道)可靠上传：业务收据表
-- 依据通信契约 v1.2 §7：数据库唯一约束 (device_id, msg_id)
-- device_id 用业务标识(Device.device_id 字符串)，与 Topic 一致；不是 devices.id(UUID)
-- digest 保留备用(内容完整性)，当前无 dtm 封装时可为空串
CREATE TABLE IF NOT EXISTS device_receipts (
  device_id varchar(100) NOT NULL,
  msg_id    varchar(200) NOT NULL,
  digest    varchar(64)  NOT NULL DEFAULT '',
  kind      varchar(30)  NOT NULL DEFAULT 'telemetry',
  created_at timestamptz DEFAULT now(),
  PRIMARY KEY (device_id, msg_id)
);

COMMENT ON TABLE  device_receipts IS '固定终端上传业务收据，落库去重；契约 v1.2 §7';
COMMENT ON COLUMN device_receipts.msg_id IS '格式 {device_id}/{boot_id}/{seq}';

-- telemetry 增加 time_quality：0=无可信时间(sampled_at未知/0)，1=有可信时间
ALTER TABLE telemetry
  ADD COLUMN IF NOT EXISTS time_quality integer DEFAULT 1;

COMMENT ON COLUMN telemetry.time_quality IS '采样时间可信度；契约v1.2 §5，0时不得绘制成1970';

-- telemetry 增加 sampled_at(可空)：存采样时间；time_quality=0 时为 NULL
-- timestamp 保留作服务器接收时间(received_at)，不混淆补传历史样本
ALTER TABLE telemetry
  ADD COLUMN IF NOT EXISTS sampled_at timestamptz;

COMMENT ON COLUMN telemetry.sampled_at IS '采样时间；契约v1.2 §5，未知/0时存 NULL，避免绘制成1970';

COMMIT;
