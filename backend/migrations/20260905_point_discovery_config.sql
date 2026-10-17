CREATE TABLE IF NOT EXISTS nav_point_discoveries (
 id varchar(36) PRIMARY KEY, robot_id varchar(100) NOT NULL,
 nav_point_code varchar(64) NOT NULL, first_seen_at timestamptz NOT NULL,
 last_seen_at timestamptz NOT NULL, seen_count integer NOT NULL DEFAULT 1,
 status varchar(20) NOT NULL DEFAULT 'unassigned', raw_data json,
 created_at timestamptz DEFAULT now(), updated_at timestamptz,
 CONSTRAINT uq_nav_point_discovery UNIQUE(robot_id,nav_point_code));
CREATE INDEX IF NOT EXISTS ix_nav_point_discoveries_robot_id ON nav_point_discoveries(robot_id);

CREATE TABLE IF NOT EXISTS robot_point_configs (
 id varchar(36) PRIMARY KEY, robot_id varchar(100) NOT NULL,
 nav_point_code varchar(64) NOT NULL, greenhouse_id varchar(36) NOT NULL REFERENCES greenhouses(id),
 point_name varchar(100) NOT NULL, sample_seconds integer NOT NULL DEFAULT 10 CHECK(sample_seconds BETWEEN 1 AND 120),
 collect_soil boolean NOT NULL DEFAULT true, collect_environment boolean NOT NULL DEFAULT true,
 take_photo boolean NOT NULL DEFAULT true, enabled boolean NOT NULL DEFAULT true, note text,
 created_at timestamptz DEFAULT now(), updated_at timestamptz,
 CONSTRAINT uq_robot_point_config UNIQUE(robot_id,nav_point_code));
CREATE INDEX IF NOT EXISTS ix_robot_point_configs_robot_id ON robot_point_configs(robot_id);

CREATE TABLE IF NOT EXISTS robot_point_config_releases (
 id varchar(36) PRIMARY KEY, robot_id varchar(100) NOT NULL,
 config_version integer NOT NULL, mode varchar(20) NOT NULL CHECK(mode IN ('discovery','operational')),
 config_sha256 varchar(64) NOT NULL, payload json NOT NULL,
 sync_status varchar(20) NOT NULL DEFAULT 'pending', sync_detail json,
 published_at timestamptz DEFAULT now(), applied_at timestamptz,
 CONSTRAINT uq_robot_point_release UNIQUE(robot_id,config_version));
CREATE INDEX IF NOT EXISTS ix_robot_point_config_releases_robot_id ON robot_point_config_releases(robot_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON nav_point_discoveries TO greenhouse;
GRANT SELECT, INSERT, UPDATE, DELETE ON robot_point_configs TO greenhouse;
GRANT SELECT, INSERT, UPDATE, DELETE ON robot_point_config_releases TO greenhouse;
