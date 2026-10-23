-- ============================================================
-- 007 解析错误分类（候选，**未在生产执行**）
-- 纯增量、幂等：全部 IF NOT EXISTS / ADD COLUMN IF NOT EXISTS
-- 依赖：无（不依赖 004/005/006）
-- ============================================================
BEGIN;

-- 1) raw_messages 增加分类码列（已有行保持 NULL，只有被拒的报文才有值）
ALTER TABLE raw_messages ADD COLUMN IF NOT EXISTS parse_error_kind VARCHAR(30);

CREATE INDEX IF NOT EXISTS ix_raw_parse_error_kind
    ON raw_messages (parse_error_kind, received_at);

-- 2) 聚合计数表：按 (设备, 分类, 报文指纹) 计数。
--    存在意义：每 30 秒一条 www.usr.cn 如果逐条落 raw_messages，一天就是 2880 行，
--    而且看不出"其实是同一条垃圾"。这张表把它压成一行 + 一个计数。
CREATE TABLE IF NOT EXISTS parse_error_stats (
    device_id    VARCHAR(100) NOT NULL,
    kind         VARCHAR(30)  NOT NULL,
    signature    VARCHAR(64)  NOT NULL,
    cnt          BIGINT       NOT NULL DEFAULT 0,
    first_seen   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_detail  TEXT,
    PRIMARY KEY (device_id, kind, signature)
);

-- 应用账号要能写这张表（迁移通常用 postgres 执行，应用连的是业务账号）
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'greenhouse') THEN
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON parse_error_stats TO greenhouse';
  END IF;
END $$;

COMMIT;
