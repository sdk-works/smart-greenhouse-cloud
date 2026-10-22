-- ============================================================
-- 2026-10-21 第三批迁移（M3 数据可靠性）
-- 内容：归属判定留痕 / 异常事件（事件单 + 告警）/ 绑定索引
-- 性质：纯增量、幂等（可重复执行）
-- 目标库：greenhouse_nightly_test（**只允许在测试库执行，严禁生产库**）
-- ============================================================
BEGIN;

-- ── 1) telemetry：归属判定留痕 ───────────────────────────────
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS attribution_reason VARCHAR(40);
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS binding_id        VARCHAR(36);
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS binding_version   INTEGER;

-- 历史 NULL 行回填：**先证据、后标记**，不再一律置 pending。
-- 依据 device_bindings 的绑定窗口（device_id + greenhouse_id + start_at/end_at）：
--   1a) 有绑定窗口覆盖 → resolved，reason=backfill_binding_window，并记 binding_id/version
--   1b) 无窗口覆盖     → pending，reason=legacy_before_attribution（不猜历史归属）
--   两种情况都写入 attribution_migrations，数量、规则、时间可审计，可回滚
CREATE TABLE IF NOT EXISTS attribution_migrations (
  id            VARCHAR(36) PRIMARY KEY,
  code          VARCHAR(60) NOT NULL,
  rule          VARCHAR(80) NOT NULL,
  rows_affected INTEGER NOT NULL DEFAULT 0,
  detail        JSON DEFAULT '{}',
  executed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_attribution_migrations_code
  ON attribution_migrations(code);

WITH covered AS (
  SELECT t.id AS telemetry_id, b.id AS binding_id, b.binding_version
    FROM telemetry t
    JOIN device_bindings b
      ON b.device_id     = t.device_id
     AND b.greenhouse_id = t.greenhouse_id
     AND b.start_at     <= t.created_at
     AND (b.end_at IS NULL OR b.end_at >= t.created_at)
   WHERE t.attribution_status IS NULL
     AND t.company_id IS NOT NULL          -- 公司为空视为证据不足，留给人工确认
), upd AS (
  UPDATE telemetry t
     SET attribution_status = 'resolved',
         attribution_reason = 'backfill_binding_window',
         binding_id         = c.binding_id,
         binding_version    = c.binding_version
    FROM covered c
   WHERE t.id = c.telemetry_id
  RETURNING 1
)
INSERT INTO attribution_migrations (id, code, rule, rows_affected, detail)
SELECT gen_random_uuid()::text, '004_backfill_null', 'binding_window', count(*),
       json_build_object('status', 'resolved', 'source', 'device_bindings')
  FROM upd;

WITH upd AS (
  UPDATE telemetry
     SET attribution_status = 'pending',
         attribution_reason = 'legacy_before_attribution'
   WHERE attribution_status IS NULL
  RETURNING 1
)
INSERT INTO attribution_migrations (id, code, rule, rows_affected, detail)
SELECT gen_random_uuid()::text, '004_backfill_null', 'no_binding_window', count(*),
       json_build_object('status', 'pending', 'source', 'none')
  FROM upd;

-- 自检：仅检查本次回填写入的行（reason=backfill_binding_window），
-- 其 company 必须与棚室所属公司一致；不一致即中止整个事务（防止历史归属被扩大）。
-- 说明：不用 status='resolved' 做条件——应用侧历史写入的 resolved 行不归本次迁移负责。
DO $$
DECLARE bad BIGINT;
BEGIN
  SELECT count(*) INTO bad
    FROM telemetry t
    JOIN greenhouses g ON g.id = t.greenhouse_id
   WHERE t.attribution_reason = 'backfill_binding_window'
     AND g.company_id IS DISTINCT FROM t.company_id;
  IF bad > 0 THEN
    RAISE EXCEPTION '归属自检失败：% 行 company 与棚室不一致，迁移已回滚', bad;
  END IF;
END $$;

-- ── 2) incidents：异常事件单 ─────────────────────────────────
CREATE TABLE IF NOT EXISTS incidents (
  id            VARCHAR(36) PRIMARY KEY,
  code          VARCHAR(40)  NOT NULL,
  title         VARCHAR(200) NOT NULL,
  category      VARCHAR(40)  NOT NULL DEFAULT 'environment',
  severity      VARCHAR(20)  NOT NULL DEFAULT 'warning',
  status        VARCHAR(20)  NOT NULL DEFAULT 'pending_confirm',
  source        VARCHAR(30)  NOT NULL DEFAULT 'auto',
  greenhouse_id VARCHAR(36),
  device_id     VARCHAR(36),
  company_id    VARCHAR(36),
  detected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload       JSON DEFAULT '{}',
  note          TEXT,
  created_by    VARCHAR(36),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_incidents_status  ON incidents(status);
CREATE INDEX IF NOT EXISTS ix_incidents_code    ON incidents(code);
CREATE INDEX IF NOT EXISTS ix_incidents_gh      ON incidents(greenhouse_id);
CREATE INDEX IF NOT EXISTS ix_incidents_company ON incidents(company_id);

-- ── 3) incident_events：状态流转历史 ─────────────────────────
CREATE TABLE IF NOT EXISTS incident_events (
  id          VARCHAR(36) PRIMARY KEY,
  incident_id VARCHAR(36) NOT NULL,
  from_status VARCHAR(20),
  to_status   VARCHAR(20) NOT NULL,
  actor_id    VARCHAR(36),
  note        TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_incident_events_incident ON incident_events(incident_id);

-- ── 4) alerts：告警（环境恢复 → 停止；与事件单关闭分开） ────
CREATE TABLE IF NOT EXISTS alerts (
  id            VARCHAR(36) PRIMARY KEY,
  incident_id   VARCHAR(36),
  rule_code     VARCHAR(60) NOT NULL,
  metric        VARCHAR(50),
  value         DOUBLE PRECISION,
  threshold     DOUBLE PRECISION,
  greenhouse_id VARCHAR(36),
  device_id     VARCHAR(36),
  company_id    VARCHAR(36),
  raised_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  cleared_at    TIMESTAMPTZ,
  clear_reason  VARCHAR(40),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_alerts_incident ON alerts(incident_id);
CREATE INDEX IF NOT EXISTS ix_alerts_gh       ON alerts(greenhouse_id);
CREATE INDEX IF NOT EXISTS ix_alerts_company  ON alerts(company_id);
CREATE INDEX IF NOT EXISTS ix_alerts_active   ON alerts(cleared_at) WHERE cleared_at IS NULL;

-- ── 5) device_bindings：补传归属判定用的索引 ────────────────
CREATE INDEX IF NOT EXISTS ix_device_bindings_device_start
  ON device_bindings(device_id, start_at);
CREATE INDEX IF NOT EXISTS ix_device_bindings_channel
  ON device_bindings(device_id, channel);

COMMIT;

-- 授权应用用户（迁移默认以 postgres 执行）
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'greenhouse') THEN
    GRANT USAGE ON SCHEMA public TO greenhouse;
    ALTER TABLE incidents        OWNER TO greenhouse;
    ALTER TABLE incident_events  OWNER TO greenhouse;
    ALTER TABLE alerts           OWNER TO greenhouse;
    GRANT SELECT, INSERT, UPDATE, DELETE ON incidents       TO greenhouse;
    GRANT SELECT, INSERT, UPDATE, DELETE ON incident_events TO greenhouse;
    GRANT SELECT, INSERT, UPDATE, DELETE ON alerts          TO greenhouse;
  END IF;
END $$;
