-- ============================================================
-- 2026-10-21 第一批迁移：固定基础关系
-- 纯增量、幂等；只加表/列，不删不改既有数据
-- 目标库：greenhouse（先在副本验证）
-- ============================================================
BEGIN;

-- ── 1) 新表 ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS companies (
  id         VARCHAR(36) PRIMARY KEY,
  name       VARCHAR(100) NOT NULL,
  status     VARCHAR(20)  NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bases (
  id         VARCHAR(36) PRIMARY KEY,
  company_id VARCHAR(36) NOT NULL REFERENCES companies(id),
  name       VARCHAR(100) NOT NULL,
  location   VARCHAR(200),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS inspection_points (
  id                VARCHAR(36) PRIMARY KEY,
  greenhouse_id     VARCHAR(36) NOT NULL REFERENCES greenhouses(id),
  code              VARCHAR(64) NOT NULL,
  name              VARCHAR(100) NOT NULL,
  zone              VARCHAR(100),
  x                 DOUBLE PRECISION,
  y                 DOUBLE PRECISION,
  coord_unit        VARCHAR(20) NOT NULL DEFAULT 'schematic',
  map_edit_revision INTEGER NOT NULL DEFAULT 0,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  retired_at        TIMESTAMPTZ,
  UNIQUE (greenhouse_id, code)
);
CREATE INDEX IF NOT EXISTS ix_inspection_points_gh ON inspection_points(greenhouse_id);

CREATE TABLE IF NOT EXISTS robot_point_mappings (
  id                  VARCHAR(36) PRIMARY KEY,
  robot_id            VARCHAR(100) NOT NULL,
  robot_map_version   VARCHAR(64),
  nav_point_code      VARCHAR(64) NOT NULL,
  point_id            VARCHAR(36) NOT NULL REFERENCES inspection_points(id),
  greenhouse_id       VARCHAR(36) NOT NULL REFERENCES greenhouses(id),
  sample_seconds      INTEGER NOT NULL DEFAULT 10,
  collect_soil        BOOLEAN NOT NULL DEFAULT true,
  collect_environment BOOLEAN NOT NULL DEFAULT true,
  take_photo          BOOLEAN NOT NULL DEFAULT true,
  enabled             BOOLEAN NOT NULL DEFAULT true,
  note                TEXT,
  effective_from      TIMESTAMPTZ NOT NULL DEFAULT now(),
  effective_to        TIMESTAMPTZ,
  status              VARCHAR(20) NOT NULL DEFAULT 'active',
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_rpm_point ON robot_point_mappings(point_id);
CREATE INDEX IF NOT EXISTS ix_rpm_robot_code ON robot_point_mappings(robot_id, nav_point_code);
CREATE UNIQUE INDEX IF NOT EXISTS uq_rpm_active
  ON robot_point_mappings(robot_id, nav_point_code)
  WHERE effective_to IS NULL;

CREATE TABLE IF NOT EXISTS device_bindings (
  id              VARCHAR(36) PRIMARY KEY,
  device_id       VARCHAR(36) NOT NULL REFERENCES devices(id),
  greenhouse_id   VARCHAR(36) NOT NULL REFERENCES greenhouses(id),
  point_id        VARCHAR(36) REFERENCES inspection_points(id),
  channel         VARCHAR(50) NOT NULL DEFAULT 'default',
  binding_version INTEGER NOT NULL DEFAULT 1,
  start_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  end_at          TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_db_device ON device_bindings(device_id, channel);
CREATE UNIQUE INDEX IF NOT EXISTS uq_db_current
  ON device_bindings(device_id, channel)
  WHERE end_at IS NULL;

CREATE TABLE IF NOT EXISTS map_versions (
  id              VARCHAR(36) PRIMARY KEY,
  greenhouse_id   VARCHAR(36) NOT NULL REFERENCES greenhouses(id),
  version         INTEGER NOT NULL,
  layout          JSON NOT NULL,
  defaults        JSON NOT NULL,
  points_snapshot JSON NOT NULL,
  schema_version  INTEGER NOT NULL DEFAULT 2,
  published_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_by    VARCHAR(36),
  immutable       BOOLEAN NOT NULL DEFAULT true,
  UNIQUE (greenhouse_id, version)
);

CREATE TABLE IF NOT EXISTS raw_messages (
  id             VARCHAR(36) PRIMARY KEY,
  msg_id         VARCHAR(100),
  source         VARCHAR(30) NOT NULL,
  device_id      VARCHAR(100),
  received_at    TIMESTAMPTZ NOT NULL,
  payload        JSONB,
  payload_bytes  INTEGER,
  parse_status   VARCHAR(20) NOT NULL DEFAULT 'stored',
  parser_version VARCHAR(20),
  error          TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_raw_device_time ON raw_messages(device_id, received_at);

-- ── 2) 新列（全部可空，避免锁表/破坏写入） ──────────────────
ALTER TABLE greenhouses ADD COLUMN IF NOT EXISTS company_id VARCHAR(36) REFERENCES companies(id);
ALTER TABLE greenhouses ADD COLUMN IF NOT EXISTS base_id    VARCHAR(36) REFERENCES bases(id);
ALTER TABLE greenhouses ADD COLUMN IF NOT EXISTS coord_unit VARCHAR(20) NOT NULL DEFAULT 'schematic';
ALTER TABLE greenhouses ADD COLUMN IF NOT EXISTS schema_version INTEGER NOT NULL DEFAULT 2;

ALTER TABLE users ADD COLUMN IF NOT EXISTS company_id VARCHAR(36) REFERENCES companies(id);
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_platform_operator BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE robot_point_configs ADD COLUMN IF NOT EXISTS point_id VARCHAR(36) REFERENCES inspection_points(id);
ALTER TABLE robot_point_configs ADD COLUMN IF NOT EXISTS robot_map_version VARCHAR(64);

ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS company_id VARCHAR(36);
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS position_quality VARCHAR(20);
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS attribution_status VARCHAR(20);
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS map_version_id VARCHAR(36);
ALTER TABLE telemetry ADD COLUMN IF NOT EXISTS raw_message_id VARCHAR(36);

-- ── 3) 默认公司/基地 ────────────────────────────────────────
INSERT INTO companies(id, name)
SELECT 'c0000000-0000-0000-0000-000000000001', '默认公司'
WHERE NOT EXISTS (SELECT 1 FROM companies);

INSERT INTO bases(id, company_id, name)
SELECT 'b0000000-0000-0000-0000-000000000001',
       'c0000000-0000-0000-0000-000000000001', '默认基地'
WHERE NOT EXISTS (SELECT 1 FROM bases);

-- ── 4) 回填归属 ─────────────────────────────────────────────
UPDATE greenhouses SET company_id = 'c0000000-0000-0000-0000-000000000001' WHERE company_id IS NULL;
UPDATE greenhouses SET base_id    = 'b0000000-0000-0000-0000-000000000001' WHERE base_id IS NULL;
UPDATE users      SET company_id = 'c0000000-0000-0000-0000-000000000001' WHERE company_id IS NULL;
UPDATE users      SET is_platform_operator = true WHERE role::text = 'SUPER_ADMIN';

-- ── 5) map_data → inspection_points（新 points 形状） ───────
INSERT INTO inspection_points
  (id, greenhouse_id, code, name, zone, x, y, coord_unit, map_edit_revision)
SELECT
  COALESCE(NULLIF(pt->>'id', ''), gen_random_uuid()::text),
  g.id,
  COALESCE(NULLIF(pt->>'code', ''), NULLIF(pt->>'id', ''), gen_random_uuid()::text),
  COALESCE(NULLIF(pt->>'point_name', ''), NULLIF(pt->>'label', ''), '未命名'),
  NULLIF(pt->>'zone', ''),
  NULLIF(pt->>'x', '')::double precision,
  NULLIF(pt->>'y', '')::double precision,
  COALESCE(g.coord_unit, 'schematic'),
  COALESCE(NULLIF(g.map_data::jsonb->>'revision', '')::int, 0)
FROM greenhouses g
CROSS JOIN LATERAL jsonb_array_elements(
  COALESCE(COALESCE(g.map_data::jsonb, '{}'::jsonb)->'points', '[]'::jsonb)
) AS pt
ON CONFLICT (greenhouse_id, code) DO NOTHING;

-- ── 5b) 旧 waypoints 形状 ───────────────────────────────────
INSERT INTO inspection_points
  (id, greenhouse_id, code, name, zone, x, y, coord_unit, map_edit_revision)
SELECT
  COALESCE(NULLIF(pt->>'id', ''), gen_random_uuid()::text),
  g.id,
  COALESCE(NULLIF(pt->>'code', ''), NULLIF(pt->>'id', ''), gen_random_uuid()::text),
  COALESCE(NULLIF(pt->>'label', ''), NULLIF(pt->>'name', ''), '未命名'),
  NULLIF(pt->>'zone', ''),
  NULLIF(pt->>'x', '')::double precision,
  NULLIF(pt->>'y', '')::double precision,
  COALESCE(g.coord_unit, 'schematic'),
  COALESCE(NULLIF(g.map_data::jsonb->>'revision', '')::int, 0)
FROM greenhouses g
CROSS JOIN LATERAL jsonb_array_elements(
  COALESCE(COALESCE(g.map_data::jsonb, '{}'::jsonb)->'waypoints', '[]'::jsonb)
) AS pt
ON CONFLICT DO NOTHING;

-- ── 6) robot_point_configs.point_id 回填（按同棚同名匹配） ──
UPDATE robot_point_configs c
SET point_id = p.id
FROM inspection_points p
WHERE c.point_id IS NULL
  AND p.greenhouse_id = c.greenhouse_id
  AND p.name = c.point_name;

-- ── 7) 由现有绑定 seed robot_point_mappings ─────────────────
INSERT INTO robot_point_mappings
  (id, robot_id, nav_point_code, point_id, greenhouse_id,
   sample_seconds, collect_soil, collect_environment, take_photo, enabled, note, status)
SELECT gen_random_uuid()::text, c.robot_id, c.nav_point_code, c.point_id, c.greenhouse_id,
       c.sample_seconds, c.collect_soil, c.collect_environment, c.take_photo, c.enabled, c.note, 'active'
FROM robot_point_configs c
WHERE c.point_id IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM robot_point_mappings m
    WHERE m.robot_id = c.robot_id AND m.nav_point_code = c.nav_point_code AND m.effective_to IS NULL
  );

-- ── 8) 由 devices.greenhouse_id seed device_bindings ────────
INSERT INTO device_bindings
  (id, device_id, greenhouse_id, channel, binding_version, start_at)
SELECT gen_random_uuid()::text, d.id, d.greenhouse_id, 'default', 1, COALESCE(d.created_at, now())
FROM devices d
WHERE d.greenhouse_id IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM device_bindings b
    WHERE b.device_id = d.id AND b.channel = 'default' AND b.end_at IS NULL
  );

-- ── 9) telemetry.company_id 回填 ────────────────────────────
UPDATE telemetry t
SET company_id = g.company_id
FROM greenhouses g
WHERE t.greenhouse_id = g.id AND t.company_id IS NULL;

-- ── 10) 条件收紧 NOT NULL（仅当无空值） ─────────────────────
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM greenhouses WHERE company_id IS NULL) THEN
    ALTER TABLE greenhouses ALTER COLUMN company_id SET NOT NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM greenhouses WHERE base_id IS NULL) THEN
    ALTER TABLE greenhouses ALTER COLUMN base_id SET NOT NULL;
  END IF;
END $$;

COMMIT;
