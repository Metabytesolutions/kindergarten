CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TYPE role_type             AS ENUM ('DIRECTOR','TEACHER','IT');
CREATE TYPE zone_type             AS ENUM ('CLASSROOM','CORRIDOR','ENTRANCE','EXIT');
CREATE TYPE tag_type              AS ENUM ('STUDENT','TEACHER','SPARE');
CREATE TYPE assigned_entity_type  AS ENUM ('STUDENT','TEACHER','NONE');
CREATE TYPE batch_state           AS ENUM ('DRAFT','READY','ACTIVE','PAUSED','CLOSED','ARCHIVED');
CREATE TYPE presence_state_type   AS ENUM ('UNKNOWN','PROBABLE_PRESENT','CONFIRMED_PRESENT','TRANSITIONING','EXIT_CONFIRMED');
CREATE TYPE gateway_health_state  AS ENUM ('HEALTHY','DEGRADED','OFFLINE');
CREATE TYPE alert_type            AS ENUM ('BROKEN_ASSOCIATION','EXIT_ATTEMPT','TAG_MISSING','GATEWAY_OFFLINE','DATA_GAP','TEACHER_NOT_ACTIVATED','UNREGISTERED_TAG_DETECTED');
CREATE TYPE alert_severity        AS ENUM ('INFO','WARNING','CRITICAL');
CREATE TYPE alert_status          AS ENUM ('OPEN','ACKED','CLOSED');

CREATE TABLE users (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  username      VARCHAR(64)  UNIQUE NOT NULL,
  email         VARCHAR(255) UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role          role_type NOT NULL,
  is_active     BOOLEAN DEFAULT TRUE,
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE zones (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name        VARCHAR(128) NOT NULL,
  zone_type   zone_type NOT NULL,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE students (
  id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  first_name     VARCHAR(64) NOT NULL,
  last_name      VARCHAR(64) NOT NULL,
  dob            DATE,
  guardian_name  VARCHAR(128),
  guardian_phone VARCHAR(20),
  is_active      BOOLEAN DEFAULT TRUE,
  created_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE ble_gateways (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  mac_address       VARCHAR(17) UNIQUE NOT NULL,
  label             VARCHAR(128),
  zone_id           UUID REFERENCES zones(id),
  ip_address        INET,
  health_state      gateway_health_state DEFAULT 'OFFLINE',
  last_heartbeat_at TIMESTAMPTZ,
  discovered_at     TIMESTAMPTZ,
  configured_at     TIMESTAMPTZ,
  verified_at       TIMESTAMPTZ,
  activated_at      TIMESTAMPTZ,
  is_active         BOOLEAN DEFAULT FALSE,
  mqtt_topic        VARCHAR(255),
  firmware_version  VARCHAR(32),
  created_at        TIMESTAMPTZ DEFAULT NOW(),
  updated_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE ble_tags (
  id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  mac_address    VARCHAR(17) UNIQUE NOT NULL,
  tag_type       tag_type NOT NULL DEFAULT 'STUDENT',
  assigned_to    assigned_entity_type DEFAULT 'NONE',
  student_id     UUID REFERENCES students(id),
  teacher_id     UUID REFERENCES users(id),
  registered_at  TIMESTAMPTZ DEFAULT NOW(),
  activated_at   TIMESTAMPTZ,
  deactivated_at TIMESTAMPTZ,
  last_seen_at   TIMESTAMPTZ,
  last_rssi      INTEGER,
  battery_mv     INTEGER,
  is_active      BOOLEAN DEFAULT FALSE,
  created_at     TIMESTAMPTZ DEFAULT NOW(),
  updated_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE batches (
  id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  classroom_id            UUID REFERENCES zones(id) NOT NULL,
  teacher_id              UUID REFERENCES users(id) NOT NULL,
  state                   batch_state DEFAULT 'DRAFT',
  roster_snapshot         JSONB,
  started_at              TIMESTAMPTZ,
  closed_at               TIMESTAMPTZ,
  activation_confirmed_at TIMESTAMPTZ,
  activation_confirmed_by UUID REFERENCES users(id),
  created_at              TIMESTAMPTZ DEFAULT NOW(),
  updated_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE detections (
  id          BIGSERIAL,
  gateway_id  UUID REFERENCES ble_gateways(id) NOT NULL,
  tag_mac     VARCHAR(17) NOT NULL,
  rssi        INTEGER,
  battery_mv  INTEGER,
  adv_count   BIGINT,
  raw_payload JSONB,
  detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (id, detected_at)
) PARTITION BY RANGE (detected_at);

CREATE TABLE detections_2026_02 PARTITION OF detections
  FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE detections_2026_03 PARTITION OF detections
  FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE detections_2026_04 PARTITION OF detections
  FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE detections_2026_05 PARTITION OF detections
  FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE detections_2026_06 PARTITION OF detections
  FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE TABLE presence_states (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  batch_id          UUID REFERENCES batches(id) NOT NULL,
  tag_id            UUID REFERENCES ble_tags(id) NOT NULL,
  student_id        UUID REFERENCES students(id),
  state             presence_state_type DEFAULT 'UNKNOWN',
  confidence_pct    SMALLINT DEFAULT 0,
  zone_id           UUID REFERENCES zones(id),
  last_detection_at TIMESTAMPTZ,
  updated_at        TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(batch_id, tag_id)
);

CREATE TABLE alerts (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  batch_id    UUID REFERENCES batches(id),
  alert_type  alert_type NOT NULL,
  severity    alert_severity NOT NULL,
  status      alert_status DEFAULT 'OPEN',
  title       VARCHAR(255) NOT NULL,
  description TEXT,
  evidence    JSONB,
  student_id  UUID REFERENCES students(id),
  gateway_id  UUID REFERENCES ble_gateways(id),
  tag_id      UUID REFERENCES ble_tags(id),
  acked_by    UUID REFERENCES users(id),
  acked_at    TIMESTAMPTZ,
  resolved_at TIMESTAMPTZ,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE audit_log (
  id          BIGSERIAL PRIMARY KEY,
  actor_id    UUID REFERENCES users(id),
  actor_role  role_type,
  action      VARCHAR(128) NOT NULL,
  entity_type VARCHAR(64),
  entity_id   UUID,
  payload     JSONB,
  ip_address  INET,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_detections_tag_mac ON detections (tag_mac, detected_at DESC);
CREATE INDEX idx_detections_gateway ON detections (gateway_id, detected_at DESC);
CREATE INDEX idx_presence_batch     ON presence_states (batch_id);
CREATE INDEX idx_alerts_open        ON alerts (batch_id, status) WHERE status = 'OPEN';
CREATE INDEX idx_tags_mac           ON ble_tags (mac_address);
CREATE INDEX idx_tags_active        ON ble_tags (is_active) WHERE is_active = TRUE;
CREATE INDEX idx_gateways_health    ON ble_gateways (health_state, last_heartbeat_at);
CREATE INDEX idx_audit_actor        ON audit_log (actor_id, created_at DESC);

INSERT INTO users (username, email, password_hash, role)
VALUES ('admin', 'admin@prosper.local',
  '$2b$12$LXkz5VFJqTEr5U9YXPkmv.qJvC0RJ/y3X.Kmj0Y4ZHX.MCEiWBaCi', 'IT');
