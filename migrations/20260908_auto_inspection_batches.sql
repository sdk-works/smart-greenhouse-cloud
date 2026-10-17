BEGIN;
CREATE TABLE IF NOT EXISTS inspection_batches (
  id varchar(36) PRIMARY KEY,
  robot_id varchar(100) NOT NULL,
  status varchar(20) NOT NULL DEFAULT 'running',
  source varchar(30) NOT NULL DEFAULT 'auto_event',
  navigation_route_label varchar(200),
  route_identification varchar(30) NOT NULL DEFAULT 'unknown',
  config_version integer,
  started_at timestamptz NOT NULL,
  last_event_at timestamptz NOT NULL,
  ended_at timestamptz,
  close_reason varchar(100),
  summary jsonb DEFAULT '{}'::jsonb,
  created_at timestamptz DEFAULT now(), updated_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_inspection_batches_robot_id ON inspection_batches(robot_id);
CREATE INDEX IF NOT EXISTS ix_inspection_batches_status ON inspection_batches(status);
CREATE INDEX IF NOT EXISTS ix_inspection_batches_last_event_at ON inspection_batches(last_event_at);

CREATE TABLE IF NOT EXISTS inspection_visits (
  id varchar(36) PRIMARY KEY,
  batch_id varchar(36) NOT NULL REFERENCES inspection_batches(id) ON DELETE CASCADE,
  robot_id varchar(100) NOT NULL,
  event_id varchar(100) NOT NULL,
  task_id varchar(100), nav_point_code varchar(64) NOT NULL,
  greenhouse_id varchar(36) REFERENCES greenhouses(id), point_name varchar(100),
  mapping_id varchar(36), config_version integer,
  status varchar(30) NOT NULL DEFAULT 'arrived',
  arrived_at timestamptz NOT NULL, sampled_at timestamptz,
  telemetry_id varchar(36) REFERENCES telemetry(id), error_code varchar(100),
  raw_event jsonb, created_at timestamptz DEFAULT now(), updated_at timestamptz,
  CONSTRAINT uq_inspection_visit_event UNIQUE(robot_id,event_id)
);
CREATE INDEX IF NOT EXISTS ix_inspection_visits_batch_id ON inspection_visits(batch_id);
CREATE INDEX IF NOT EXISTS ix_inspection_visits_robot_id ON inspection_visits(robot_id);
CREATE INDEX IF NOT EXISTS ix_inspection_visits_task_id ON inspection_visits(task_id);
CREATE INDEX IF NOT EXISTS ix_inspection_visits_nav_point_code ON inspection_visits(nav_point_code);
COMMIT;
