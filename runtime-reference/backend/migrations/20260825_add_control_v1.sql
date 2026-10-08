DO $$ BEGIN
  CREATE TYPE controlmode AS ENUM ('manual','auto','maintenance');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE TYPE commandstatus AS ENUM ('pending','sent','accepted','executing','executed','rejected','timeout','failed','cancelled');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS actuator_profiles (
  device_id varchar(36) PRIMARY KEY REFERENCES devices(id), zone_id varchar(100),
  control_mode controlmode NOT NULL DEFAULT 'manual', desired_state json DEFAULT '{}', actual_state json DEFAULT '{}',
  safety_limits json DEFAULT '{}', emergency_stop boolean NOT NULL DEFAULT false,
  maintenance_lock boolean NOT NULL DEFAULT false, last_command_id varchar(100), state_updated_at timestamptz,
  created_at timestamptz DEFAULT now(), updated_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_actuator_profiles_zone_id ON actuator_profiles(zone_id);

CREATE TABLE IF NOT EXISTS actuator_commands (
  id varchar(36) PRIMARY KEY, command_id varchar(100) UNIQUE NOT NULL,
  device_id varchar(36) NOT NULL REFERENCES devices(id), greenhouse_id varchar(36) REFERENCES greenhouses(id),
  action varchar(50) NOT NULL, params json DEFAULT '{}', source varchar(30) NOT NULL DEFAULT 'manual', reason text,
  status commandstatus NOT NULL DEFAULT 'pending', desired_state json DEFAULT '{}', actual_state json DEFAULT '{}',
  issued_by varchar(36) REFERENCES users(id), issued_at timestamptz DEFAULT now(), expires_at timestamptz NOT NULL,
  accepted_at timestamptz, completed_at timestamptz, error_message text,
  created_at timestamptz DEFAULT now(), updated_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_actuator_commands_command_id ON actuator_commands(command_id);
CREATE INDEX IF NOT EXISTS ix_actuator_commands_device_id ON actuator_commands(device_id);
CREATE INDEX IF NOT EXISTS ix_actuator_commands_status ON actuator_commands(status);

CREATE TABLE IF NOT EXISTS control_rules (
  id varchar(36) PRIMARY KEY, name varchar(200) NOT NULL,
  greenhouse_id varchar(36) NOT NULL REFERENCES greenhouses(id), device_id varchar(36) NOT NULL REFERENCES devices(id),
  enabled boolean NOT NULL DEFAULT false, metric varchar(50) NOT NULL, start_operator varchar(5) NOT NULL,
  start_threshold double precision NOT NULL, stop_threshold double precision NOT NULL,
  duration_seconds integer NOT NULL DEFAULT 60, cooldown_seconds integer NOT NULL DEFAULT 300,
  priority integer NOT NULL DEFAULT 5, action varchar(50) NOT NULL, action_params json DEFAULT '{}',
  created_by varchar(36) REFERENCES users(id), created_at timestamptz DEFAULT now(), updated_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_control_rules_greenhouse_id ON control_rules(greenhouse_id);
CREATE INDEX IF NOT EXISTS ix_control_rules_device_id ON control_rules(device_id);
