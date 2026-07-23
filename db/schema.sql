CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS log_records (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    level TEXT NOT NULL,
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    run_id INTEGER,
    model_id TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingest_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_log_records_ts ON log_records (ts);
CREATE INDEX IF NOT EXISTS idx_log_records_level_ts ON log_records (level, ts);
CREATE INDEX IF NOT EXISTS idx_log_records_category_ts ON log_records (category, ts);

CREATE TABLE IF NOT EXISTS detection_events (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    run_id INTEGER,
    model_id TEXT NOT NULL,
    object_count INTEGER NOT NULL,
    elapsed_ms REAL,
    dedup TEXT,
    repeated_frames INTEGER,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingest_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_detection_events_model_ts ON detection_events (model_id, ts);
CREATE INDEX IF NOT EXISTS idx_detection_events_run ON detection_events (run_id);

CREATE TABLE IF NOT EXISTS detected_objects (
    id BIGSERIAL PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES detection_events(id) ON DELETE CASCADE,
    ts TIMESTAMPTZ NOT NULL,
    run_id INTEGER,
    model_id TEXT NOT NULL,
    class_name TEXT NOT NULL,
    confidence REAL,
    bbox REAL[],
    area_ratio REAL
);
CREATE INDEX IF NOT EXISTS idx_detected_objects_class_ts ON detected_objects (class_name, ts);
CREATE INDEX IF NOT EXISTS idx_detected_objects_model_ts ON detected_objects (model_id, ts);
CREATE INDEX IF NOT EXISTS idx_detected_objects_event ON detected_objects (event_id);

CREATE TABLE IF NOT EXISTS media_blobs (
    id BIGSERIAL PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    mime TEXT NOT NULL DEFAULT 'image/jpeg',
    width INTEGER NOT NULL CHECK (width > 0),
    height INTEGER NOT NULL CHECK (height > 0),
    byte_size INTEGER NOT NULL CHECK (byte_size > 0),
    data BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS media_captures (
    capture_id UUID PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    run_id INTEGER,
    source_name TEXT,
    source_kind TEXT,
    frame_sequence INTEGER,
    is_reprocess BOOLEAN NOT NULL DEFAULT FALSE,
    original_media_id BIGINT NOT NULL REFERENCES media_blobs(id) ON DELETE RESTRICT,
    annotated_media_id BIGINT NOT NULL REFERENCES media_blobs(id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_media_captures_ts ON media_captures (ts);
CREATE INDEX IF NOT EXISTS idx_media_captures_original ON media_captures (original_media_id);
CREATE INDEX IF NOT EXISTS idx_media_captures_annotated ON media_captures (annotated_media_id);

CREATE TABLE IF NOT EXISTS media_capture_models (
    capture_id UUID NOT NULL REFERENCES media_captures(capture_id) ON DELETE CASCADE,
    model_id TEXT NOT NULL,
    signature JSONB,
    object_count INTEGER NOT NULL CHECK (object_count > 0),
    PRIMARY KEY (capture_id, model_id)
);
CREATE INDEX IF NOT EXISTS idx_media_capture_models_model
    ON media_capture_models (model_id, capture_id);

ALTER TABLE detection_events
    ADD COLUMN IF NOT EXISTS capture_id UUID;
CREATE INDEX IF NOT EXISTS idx_detection_events_capture
    ON detection_events (capture_id);

INSERT INTO schema_info (version)
VALUES (1), (2)
ON CONFLICT (version) DO NOTHING;
