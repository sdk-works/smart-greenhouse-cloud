-- ============================================================
-- 2026-10-21 第四批迁移（M4 业务模块）
-- 内容：种植批次 / 指标定义（含最小集种子）/ AI 建议留痕
-- 性质：纯增量、幂等（可重复执行）
-- 目标库：greenhouse_nightly_test（**只允许在测试库执行，严禁生产库**）
-- ============================================================
BEGIN;

-- ── 1) planting_batches ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS planting_batches (
  id                  VARCHAR(36) PRIMARY KEY,
  code                VARCHAR(60)  NOT NULL,
  greenhouse_id       VARCHAR(36)  NOT NULL,
  zone                VARCHAR(100),
  crop_type           VARCHAR(100) NOT NULL,
  variety             VARCHAR(100),
  area                DOUBLE PRECISION,
  status              VARCHAR(20)  NOT NULL DEFAULT 'planned',
  planted_at          TIMESTAMPTZ,
  expected_harvest_at TIMESTAMPTZ,
  harvested_at        TIMESTAMPTZ,
  note                TEXT,
  company_id          VARCHAR(36),
  created_by          VARCHAR(36),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_planting_batches_gh      ON planting_batches(greenhouse_id);
CREATE INDEX IF NOT EXISTS ix_planting_batches_status  ON planting_batches(status);
CREATE INDEX IF NOT EXISTS ix_planting_batches_company ON planting_batches(company_id);
CREATE INDEX IF NOT EXISTS ix_planting_batches_code    ON planting_batches(code);

-- ── 2) metric_definitions（指标定义最小集） ──────────────────
CREATE TABLE IF NOT EXISTS metric_definitions (
  id            VARCHAR(36) PRIMARY KEY,
  code          VARCHAR(60)  NOT NULL,
  name          VARCHAR(100) NOT NULL,
  unit          VARCHAR(20),
  value_type    VARCHAR(20)  NOT NULL DEFAULT 'float',
  min_value     DOUBLE PRECISION,
  max_value     DOUBLE PRECISION,
  precision     INTEGER      NOT NULL DEFAULT 2,
  source        VARCHAR(30)  NOT NULL DEFAULT 'sensor',
  quality_rules JSON         DEFAULT '{}',
  enabled       BOOLEAN      NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_metric_definition_code ON metric_definitions(code);

-- 最小集种子（幂等：已存在则跳过）
INSERT INTO metric_definitions (id, code, name, unit, value_type, min_value, max_value, precision)
SELECT * FROM (VALUES
  (md5(random()::text)::varchar(36), 'temperature',       '空气温度', '°C',    'float', -10::double precision,  60::double precision, 1),
  (md5(random()::text)::varchar(36), 'humidity',          '空气湿度', '%',     'float',   0::double precision, 100::double precision, 1),
  (md5(random()::text)::varchar(36), 'soil_moisture',     '土壤湿度', '%',     'float',   0::double precision, 100::double precision, 1),
  (md5(random()::text)::varchar(36), 'soil_temperature',  '土壤温度', '°C',    'float', -10::double precision,  60::double precision, 1),
  (md5(random()::text)::varchar(36), 'soil_ec',           '土壤电导率', 'mS/cm','float',  0::double precision,  10::double precision, 2),
  (md5(random()::text)::varchar(36), 'ph',                'pH 值',   '',       'float',   0::double precision,  14::double precision, 2),
  (md5(random()::text)::varchar(36), 'light',             '光照强度', 'lux',   'float',   0::double precision, 200000::double precision, 0),
  (md5(random()::text)::varchar(36), 'co2',               'CO₂ 浓度', 'ppm',   'float',   0::double precision, 5000::double precision, 0)
) AS v(id, code, name, unit, value_type, min_value, max_value, precision)
WHERE NOT EXISTS (SELECT 1 FROM metric_definitions m WHERE m.code = v.code);

-- 目标区间（AI 判读用）：物理合理区间之外再给一套农艺目标区间
UPDATE metric_definitions SET quality_rules = '{"target_min": 15, "target_max": 30}'::json
  WHERE code='temperature'      AND coalesce(quality_rules::text,'{}') IN ('{}','null');
UPDATE metric_definitions SET quality_rules = '{"target_min": 50, "target_max": 80}'::json
  WHERE code='humidity'         AND coalesce(quality_rules::text,'{}') IN ('{}','null');
UPDATE metric_definitions SET quality_rules = '{"target_min": 30, "target_max": 75}'::json
  WHERE code='soil_moisture'    AND coalesce(quality_rules::text,'{}') IN ('{}','null');
UPDATE metric_definitions SET quality_rules = '{"target_min": 12, "target_max": 28}'::json
  WHERE code='soil_temperature' AND coalesce(quality_rules::text,'{}') IN ('{}','null');
UPDATE metric_definitions SET quality_rules = '{"target_min": 0.2, "target_max": 2.5}'::json
  WHERE code='soil_ec'          AND coalesce(quality_rules::text,'{}') IN ('{}','null');
UPDATE metric_definitions SET quality_rules = '{"target_min": 5.5, "target_max": 7.5}'::json
  WHERE code='ph'               AND coalesce(quality_rules::text,'{}') IN ('{}','null');
UPDATE metric_definitions SET quality_rules = '{"target_min": 2000, "target_max": 40000}'::json
  WHERE code='light'            AND coalesce(quality_rules::text,'{}') IN ('{}','null');
UPDATE metric_definitions SET quality_rules = '{"target_min": 400, "target_max": 1200}'::json
  WHERE code='co2'              AND coalesce(quality_rules::text,'{}') IN ('{}','null');

-- ── 3) ai_advice_logs（AI 建议留痕） ─────────────────────────
CREATE TABLE IF NOT EXISTS ai_advice_logs (
  id              VARCHAR(36) PRIMARY KEY,
  greenhouse_id   VARCHAR(36),
  kind            VARCHAR(40) NOT NULL DEFAULT 'advice',
  request         JSON DEFAULT '{}',
  advice          JSON DEFAULT '[]',
  basis           JSON DEFAULT '{}',
  db_access_used  BOOLEAN NOT NULL DEFAULT FALSE,
  control_issued  BOOLEAN NOT NULL DEFAULT FALSE,
  requested_by    VARCHAR(36),
  company_id      VARCHAR(36),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ai_advice_logs_gh      ON ai_advice_logs(greenhouse_id);
CREATE INDEX IF NOT EXISTS ix_ai_advice_logs_company ON ai_advice_logs(company_id);

COMMIT;

-- 授权应用用户（迁移默认以 postgres 执行）
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'greenhouse') THEN
    GRANT USAGE ON SCHEMA public TO greenhouse;
    ALTER TABLE planting_batches  OWNER TO greenhouse;
    ALTER TABLE metric_definitions OWNER TO greenhouse;
    ALTER TABLE ai_advice_logs    OWNER TO greenhouse;
    GRANT SELECT, INSERT, UPDATE, DELETE ON planting_batches   TO greenhouse;
    GRANT SELECT, INSERT, UPDATE, DELETE ON metric_definitions TO greenhouse;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ai_advice_logs     TO greenhouse;
  END IF;
END $$;
