-- ============================================================
-- 2026-10-21 第二批迁移（M2 机器人闭环）
-- 内容：照片归档列 / 巡检证据 / 任务快照 / 任务状态列去枚举化 /
--       机器人地图版本登记与告警
-- 性质：纯增量、幂等（可重复执行）；状态列会做类型转换，需先备份
-- 目标库：greenhouse_nightly_test（**只允许在测试库执行，严禁生产库**）
-- ============================================================
BEGIN;

-- ── 1) robot_tasks：状态改为 VARCHAR + 任务快照 ───────────────
-- 原生枚举类型 taskstatus 无法在事务内加值，且状态机已由代码层强制，
-- 这里统一改成 VARCHAR(20)。
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'taskstatus') THEN
    ALTER TABLE robot_tasks
      ALTER COLUMN status TYPE VARCHAR(20) USING status::text;
    DROP TYPE taskstatus;
  END IF;
END $$;

ALTER TABLE robot_tasks ADD COLUMN IF NOT EXISTS snapshot       JSON;
ALTER TABLE robot_tasks ADD COLUMN IF NOT EXISTS map_version_id VARCHAR(36);

ALTER TABLE robot_tasks ALTER COLUMN status SET DEFAULT 'pending';
UPDATE robot_tasks SET status = 'pending' WHERE status IS NULL;
ALTER TABLE robot_tasks ALTER COLUMN status SET NOT NULL;

CREATE INDEX IF NOT EXISTS ix_robot_tasks_status         ON robot_tasks(status);
CREATE INDEX IF NOT EXISTS ix_robot_tasks_map_version_id ON robot_tasks(map_version_id);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_robot_tasks_map_version_id') THEN
    ALTER TABLE robot_tasks ADD CONSTRAINT fk_robot_tasks_map_version_id
      FOREIGN KEY (map_version_id) REFERENCES map_versions(id);
  END IF;
END $$;

-- ── 2) images：照片归档（点位 / 地图版本 / 批次 / 公司） ──────
ALTER TABLE images ADD COLUMN IF NOT EXISTS point_id       VARCHAR(36);
ALTER TABLE images ADD COLUMN IF NOT EXISTS map_version_id VARCHAR(36);
ALTER TABLE images ADD COLUMN IF NOT EXISTS batch_id       VARCHAR(36);
ALTER TABLE images ADD COLUMN IF NOT EXISTS company_id     VARCHAR(36);

CREATE INDEX IF NOT EXISTS ix_images_point_id        ON images(point_id);
CREATE INDEX IF NOT EXISTS ix_images_map_version_id  ON images(map_version_id);
CREATE INDEX IF NOT EXISTS ix_images_company_id      ON images(company_id);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_images_point_id') THEN
    ALTER TABLE images ADD CONSTRAINT fk_images_point_id
      FOREIGN KEY (point_id) REFERENCES inspection_points(id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_images_map_version_id') THEN
    ALTER TABLE images ADD CONSTRAINT fk_images_map_version_id
      FOREIGN KEY (map_version_id) REFERENCES map_versions(id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_images_batch_id') THEN
    ALTER TABLE images ADD CONSTRAINT fk_images_batch_id
      FOREIGN KEY (batch_id) REFERENCES inspection_batches(id);
  END IF;
END $$;

-- ── 3) inspection_evidence：巡检证据 ─────────────────────────
CREATE TABLE IF NOT EXISTS inspection_evidence (
  id                  VARCHAR(36) PRIMARY KEY,
  task_id             VARCHAR(36),
  batch_id            VARCHAR(36),
  point_id            VARCHAR(36),
  greenhouse_id       VARCHAR(36),
  robot_id            VARCHAR(100),
  nav_point_code      VARCHAR(64),
  map_version_id      VARCHAR(36),
  evidence_type       VARCHAR(30) NOT NULL DEFAULT 'photo',
  attribution_status  VARCHAR(20) NOT NULL DEFAULT 'pending',
  media               JSON NOT NULL DEFAULT '{}',
  captured_at         TIMESTAMPTZ,
  company_id          VARCHAR(36),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_inspection_evidence_task_id  ON inspection_evidence(task_id);
CREATE INDEX IF NOT EXISTS ix_inspection_evidence_point_id ON inspection_evidence(point_id);
CREATE INDEX IF NOT EXISTS ix_inspection_evidence_gh       ON inspection_evidence(greenhouse_id);
CREATE INDEX IF NOT EXISTS ix_inspection_evidence_robot    ON inspection_evidence(robot_id);
CREATE INDEX IF NOT EXISTS ix_inspection_evidence_company  ON inspection_evidence(company_id);

-- ── 4) robot_map_registry：平台登记的机器人地图版本 ───────────
CREATE TABLE IF NOT EXISTS robot_map_registry (
  id             VARCHAR(36) PRIMARY KEY,
  robot_id       VARCHAR(100) NOT NULL,
  greenhouse_id  VARCHAR(36),
  map_version    VARCHAR(64) NOT NULL,
  source         VARCHAR(30) NOT NULL DEFAULT 'platform',
  registered_by  VARCHAR(36),
  note           TEXT,
  active         BOOLEAN NOT NULL DEFAULT TRUE,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_robot_map_registry_robot ON robot_map_registry(robot_id);

-- ── 5) robot_map_alerts：地图版本不一致告警（只记录，不自动改） ─
CREATE TABLE IF NOT EXISTS robot_map_alerts (
  id                   VARCHAR(36) PRIMARY KEY,
  robot_id             VARCHAR(100) NOT NULL,
  expected_map_version VARCHAR(64),
  reported_map_version VARCHAR(64),
  source               VARCHAR(30) NOT NULL DEFAULT 'config_ack',
  status               VARCHAR(20) NOT NULL DEFAULT 'open',
  detail               JSON,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_robot_map_alerts_robot  ON robot_map_alerts(robot_id);
CREATE INDEX IF NOT EXISTS ix_robot_map_alerts_status ON robot_map_alerts(status);

COMMIT;

-- 授权应用用户（迁移默认以 postgres 执行，新建表属主会是 postgres，
-- 而应用以 greenhouse 连接，无法在其上建索引/改结构）
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'greenhouse') THEN
    GRANT USAGE ON SCHEMA public TO greenhouse;
    ALTER TABLE inspection_evidence OWNER TO greenhouse;
    ALTER TABLE robot_map_registry  OWNER TO greenhouse;
    ALTER TABLE robot_map_alerts    OWNER TO greenhouse;
    GRANT SELECT, INSERT, UPDATE, DELETE ON inspection_evidence TO greenhouse;
    GRANT SELECT, INSERT, UPDATE, DELETE ON robot_map_registry  TO greenhouse;
    GRANT SELECT, INSERT, UPDATE, DELETE ON robot_map_alerts    TO greenhouse;
  END IF;
END $$;
