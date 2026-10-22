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
-- 依据 device_bindings 的绑定窗口（device_id + greenhouse_id + start_at/end_at）。
--
-- 【时间基准 —— 2026-10-22 第二次更正，必读】
-- 本系统里 telemetry."timestamp" **不是采集时间，是服务器接收时间**。
-- 证据（app/mqtt/mqtt_service.py::_handle_device_telemetry，固定终端 agri/device/{id}/up）：
--     received_at = datetime.now(timezone.utc)
--     t = Telemetry(..., timestamp = received_at,   # 服务器接收时间
--                        sampled_at = sampled_at,   # 采样时间，未知为 NULL
--                        time_quality = time_quality)
-- 而 sampled_at 只在 time_quality<>0 时才写入；本批设备长期上报 sampled_at=0 / time_quality=0。
--
-- 生产核对（2026-10-22，7053 行 attribution_status IS NULL）：
--   · time_quality=0 且 sampled_at 为空 : 7047 行
--   · time_quality=1 且有 sampled_at    :    6 行
--   · timestamp 与 created_at 的差       : 全部落在 1~4ms（就是"赋值 → INSERT"的时间）
--   · 7053/7053 行都能在 device_receipts 找到对应回执（该回执只由固定终端分支写）
-- 结论：对这 7047 行，可用的时间证据只有**接收时间**，粒度是"设备连上网的那一刻"，
--   不是"传感器采样的那一刻"。因此：
--     sampled_at 存在      → 按采集时刻判定，reason=backfill_sample_time（可信）
--     sampled_at 为空      → 只能按接收时间推定，reason=backfill_receive_time_estimated
--                            （**推定**，不是"可信采集时间命中绑定"）
--   对于实时直传的在线时段，接收时间与采集时间相差 < 数秒，推定可用；
--   但换棚、解绑后补传、设备时钟错乱这几种情况，接收时间会与真实采集时刻脱钩，
--   所以推定行必须带 binding_id/version 与 receive_lag_sec，可逐条复核、可回滚。
--
-- 【分支】
--   1a) 恰好 1 个窗口覆盖 → resolved，reason 按时间证据分成两类（见上）
--   1b) 多个窗口重叠     → pending + reason=conflict_binding_window（报冲突，不随便取一条）
--   1c) 无窗口 / 时间缺失 → pending + reason=legacy_before_attribution / legacy_time_missing
--   每个分支都写 attribution_migrations（数量、规则、时间基准、推定行数可审计，可回滚）
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

-- 1a) 唯一窗口覆盖
WITH cand AS (
  SELECT t.id AS telemetry_id,
         b.id AS binding_id,
         b.binding_version,
         b.start_at AS win_start,
         (t.sampled_at IS NOT NULL) AS by_sampled_at,
         count(*) OVER (PARTITION BY t.id) AS win_cnt
    FROM telemetry t
    JOIN device_bindings b
      ON b.device_id     = t.device_id
     AND b.greenhouse_id = t.greenhouse_id
     AND b.start_at     <= coalesce(t.sampled_at, t."timestamp")
     AND (b.end_at IS NULL OR b.end_at >= coalesce(t.sampled_at, t."timestamp"))
   WHERE t.attribution_status IS NULL
     AND t.company_id IS NOT NULL
), covered AS (
  SELECT telemetry_id, binding_id, binding_version, win_start, by_sampled_at
    FROM cand
   WHERE win_cnt = 1
), upd AS (
  UPDATE telemetry t
     SET attribution_status = 'resolved',
         -- 有采集时间 = 可信；只有接收时间 = 推定（字段名本身就把性质写清楚）
         attribution_reason = CASE WHEN c.by_sampled_at
                                   THEN 'backfill_sample_time'
                                   ELSE 'backfill_receive_time_estimated' END,
         binding_id         = c.binding_id,
         binding_version    = c.binding_version
    FROM covered c
   WHERE t.id = c.telemetry_id
  RETURNING c.by_sampled_at,
            EXTRACT(EPOCH FROM (t."timestamp" - c.win_start))::numeric(12,3) AS lag_sec
)
INSERT INTO attribution_migrations (id, code, rule, rows_affected, detail)
SELECT gen_random_uuid()::text, '004_backfill_null', 'binding_window_by_evidence', count(*),
       json_build_object(
         'status', 'resolved',
         'source', 'device_bindings',
         'time_basis', 'sampled_at if present else receive_time(timestamp)',
         'by_sample_time',             count(*) FILTER (WHERE by_sampled_at),
         'by_receive_time_estimated',  count(*) FILTER (WHERE NOT by_sampled_at),
         'estimated_flag',             (count(*) FILTER (WHERE NOT by_sampled_at)) > 0,
         'receive_lag_sec_min',        min(lag_sec) FILTER (WHERE NOT by_sampled_at),
         'receive_lag_sec_max',        max(lag_sec) FILTER (WHERE NOT by_sampled_at),
         -- 距窗口起点 <60s 的推定行最容易把"刚绑定的历史补传"错认成"绑定后采集"，单列出来
         'estimated_near_window_start', count(*) FILTER (WHERE NOT by_sampled_at AND lag_sec < 60)
       )
  FROM upd
HAVING count(*) > 0;

-- 1b) 多窗口重叠：必须报冲突，不能任取一条
WITH conflict AS (
  SELECT t.id AS telemetry_id
    FROM telemetry t
    JOIN device_bindings b
      ON b.device_id     = t.device_id
     AND b.greenhouse_id = t.greenhouse_id
     AND b.start_at     <= coalesce(t.sampled_at, t."timestamp")
     AND (b.end_at IS NULL OR b.end_at >= coalesce(t.sampled_at, t."timestamp"))
   WHERE t.attribution_status IS NULL
     AND t.company_id IS NOT NULL
   GROUP BY t.id
  HAVING count(*) > 1
), upd AS (
  UPDATE telemetry
     SET attribution_status = 'pending',
         attribution_reason = 'conflict_binding_window'
   WHERE id IN (SELECT telemetry_id FROM conflict)
  RETURNING 1
)
INSERT INTO attribution_migrations (id, code, rule, rows_affected, detail)
SELECT gen_random_uuid()::text, '004_backfill_null', 'conflict_binding_window', count(*),
       json_build_object('status', 'pending', 'source', 'device_bindings',
                         'reason', '多个绑定窗口重叠，需人工确认')
  FROM upd
HAVING count(*) > 0;

-- 1c) 无窗口覆盖 或 时间证据缺失：保持待确认，不猜历史归属
WITH upd AS (
  UPDATE telemetry
     SET attribution_status = 'pending',
         attribution_reason = CASE
           WHEN coalesce(sampled_at, "timestamp") IS NULL THEN 'legacy_time_missing'
           ELSE 'legacy_before_attribution'
         END
   WHERE attribution_status IS NULL
  RETURNING attribution_reason
)
INSERT INTO attribution_migrations (id, code, rule, rows_affected, detail)
SELECT gen_random_uuid()::text, '004_backfill_null', 'leave_pending', count(*),
       json_build_object('status', 'pending', 'source', 'none',
                         'time_missing', count(*) FILTER (WHERE attribution_reason = 'legacy_time_missing'),
                         'no_window',    count(*) FILTER (WHERE attribution_reason = 'legacy_before_attribution'))
  FROM upd
HAVING count(*) > 0;

-- 自检：仅检查本次回填写入的行（两类 reason），其 company 必须与棚室所属公司一致；
-- 不一致即中止整个事务（防止历史归属被扩大）。
-- 说明：不用 status='resolved' 做条件——应用侧历史写入的 resolved 行不归本次迁移负责。
DO $$
DECLARE bad BIGINT;
BEGIN
  SELECT count(*) INTO bad
    FROM telemetry t
    JOIN greenhouses g ON g.id = t.greenhouse_id
   WHERE t.attribution_reason IN ('backfill_sample_time','backfill_receive_time_estimated')
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
