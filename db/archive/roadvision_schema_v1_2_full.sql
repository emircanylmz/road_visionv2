\set ON_ERROR_STOP on

/*
RoadVision v1.2.0 — PostgreSQL 17 tam şema, tespit kataloğu ve sorgular
Tarih: 2026-07-24

ARŞİV UYARISI:
  Bu dosya yalnız eski DB şema 1/2 kurulumlarını belgelemek içindir.
  RoadVision v1.2.1 / DB şema 3 üzerinde çalıştırmayın. Güncel kurulum için
  db/roadvision_schema_v1_2_1.sql dosyasını kullanın.

Çekirdek uygulama şeması:
  7 tablo, schema_info sürümü 2

Aktif model/tür envanteri:
  roadline       :  1 tür  (semantic)
  traffic_sign   : 16 tür  (detect)
  pothole        :  2 tür  (detect)
  marking_damage :  1 tür  (detect)
  TOPLAM         : 20 tür

Bu dosya:
  1. RoadVision'ın mevcut v2 çekirdek şemasını idempotent olarak kurar.
  2. Uygulamayı değiştirmeden raporlama için model/tür kataloglarını ekler.
  3. Sık kullanılan sayım ve medya sorguları için görünümler oluşturur.
  4. Dosyanın sonunda pgAdmin/psql için hazır sorgular sunar.

Önemli:
  - Referans katalog tabloları uygulama migration sürümünü artırmaz.
  - schema_info yalnız 1 ve 2 içerir; RoadVision SCHEMA_VERSION=2 ile uyumludur.
  - detected_objects üzerinde katalog FK'si bilinçli olarak yoktur. Böylece
    ileride eklenen veya eski kayıtlardan gelen bilinmeyen sınıflar kaybolmaz.
*/

BEGIN;

DO $$
DECLARE
    v_version INTEGER;
BEGIN
    IF to_regclass('schema_info') IS NOT NULL THEN
        EXECUTE
            'SELECT COALESCE(MAX(version), 0) FROM schema_info'
        INTO v_version;
        IF v_version > 2 THEN
            RAISE EXCEPTION
                'Arşiv v1.2 şeması DB şema % üzerinde çalıştırılamaz',
                v_version;
        END IF;
    END IF;
END $$;

-- ============================================================================
-- 1. ÇEKİRDEK ROADVISION ŞEMASI (mevcut db/schema.sql ile uyumlu)
-- ============================================================================

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

CREATE INDEX IF NOT EXISTS idx_log_records_ts
    ON log_records (ts);
CREATE INDEX IF NOT EXISTS idx_log_records_level_ts
    ON log_records (level, ts);
CREATE INDEX IF NOT EXISTS idx_log_records_category_ts
    ON log_records (category, ts);

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

CREATE INDEX IF NOT EXISTS idx_detection_events_model_ts
    ON detection_events (model_id, ts);
CREATE INDEX IF NOT EXISTS idx_detection_events_run
    ON detection_events (run_id);

CREATE TABLE IF NOT EXISTS detected_objects (
    id BIGSERIAL PRIMARY KEY,
    event_id BIGINT NOT NULL
        REFERENCES detection_events(id) ON DELETE CASCADE,
    ts TIMESTAMPTZ NOT NULL,
    run_id INTEGER,
    model_id TEXT NOT NULL,
    class_name TEXT NOT NULL,
    confidence REAL,
    bbox REAL[],
    area_ratio REAL
);

CREATE INDEX IF NOT EXISTS idx_detected_objects_class_ts
    ON detected_objects (class_name, ts);
CREATE INDEX IF NOT EXISTS idx_detected_objects_model_ts
    ON detected_objects (model_id, ts);
CREATE INDEX IF NOT EXISTS idx_detected_objects_event
    ON detected_objects (event_id);

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
    original_media_id BIGINT NOT NULL
        REFERENCES media_blobs(id) ON DELETE RESTRICT,
    annotated_media_id BIGINT NOT NULL
        REFERENCES media_blobs(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_media_captures_ts
    ON media_captures (ts);
CREATE INDEX IF NOT EXISTS idx_media_captures_original
    ON media_captures (original_media_id);
CREATE INDEX IF NOT EXISTS idx_media_captures_annotated
    ON media_captures (annotated_media_id);

CREATE TABLE IF NOT EXISTS media_capture_models (
    capture_id UUID NOT NULL
        REFERENCES media_captures(capture_id) ON DELETE CASCADE,
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

-- Model ayrımı olmadan zaman aralığı tarayan raporların indeks ihtiyacı.
CREATE INDEX IF NOT EXISTS idx_detection_events_ts
    ON detection_events (ts);
CREATE INDEX IF NOT EXISTS idx_detected_objects_ts
    ON detected_objects (ts);

-- ============================================================================
-- 2. MODEL VE TESPİT TÜRÜ REFERANS KATALOĞU
-- ============================================================================

CREATE TABLE IF NOT EXISTS roadvision_model_catalog (
    model_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    task TEXT NOT NULL CHECK (task IN ('semantic', 'detect')),
    class_count INTEGER NOT NULL CHECK (class_count > 0),
    input_size INTEGER NOT NULL CHECK (input_size > 0),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS roadvision_detection_type_catalog (
    model_id TEXT NOT NULL
        REFERENCES roadvision_model_catalog(model_id) ON DELETE CASCADE,
    class_index INTEGER NOT NULL CHECK (class_index >= 0),
    class_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    PRIMARY KEY (model_id, class_index),
    UNIQUE (model_id, class_name)
);

INSERT INTO roadvision_model_catalog
    (model_id, display_name, task, class_count, input_size, active)
VALUES
    ('roadline',       'Yol Çizgisi Segmentasyonu',    'semantic',  1, 1024, TRUE),
    ('traffic_sign',   'Tabela ve Trafik Işığı',       'detect',   16,  640, TRUE),
    ('pothole',        'Çukur ve Rögar Kapağı Tespiti', 'detect',    2,  768, TRUE),
    ('marking_damage', 'Yol İşareti Hasarı',           'detect',    1,  640, TRUE)
ON CONFLICT (model_id) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    task = EXCLUDED.task,
    class_count = EXCLUDED.class_count,
    input_size = EXCLUDED.input_size,
    active = EXCLUDED.active,
    updated_at = now();

INSERT INTO roadvision_detection_type_catalog
    (model_id, class_index, class_name, display_name)
VALUES
    ('roadline',        0, 'roadline',            'Yol çizgisi'),

    ('traffic_sign',    0, '20',                  'Hız sınırı 20'),
    ('traffic_sign',    1, '30',                  'Hız sınırı 30'),
    ('traffic_sign',    2, 'dur',                 'Dur'),
    ('traffic_sign',    3, 'durak',               'Durak'),
    ('traffic_sign',    4, 'girisyok',            'Giriş yok'),
    ('traffic_sign',    5, 'ilerisag',            'İleri veya sağ'),
    ('traffic_sign',    6, 'ilerisol',            'İleri veya sol'),
    ('traffic_sign',    7, 'kirmizi',             'Kırmızı ışık'),
    ('traffic_sign',    8, 'park',                'Park'),
    ('traffic_sign',    9, 'parkyasak',           'Park yasak'),
    ('traffic_sign',   10, 'sag',                 'Sağ yön'),
    ('traffic_sign',   11, 'sagadonulmez',        'Sağa dönülmez'),
    ('traffic_sign',   12, 'sari',                'Sarı ışık'),
    ('traffic_sign',   13, 'sol',                 'Sol yön'),
    ('traffic_sign',   14, 'soladonulmez',        'Sola dönülmez'),
    ('traffic_sign',   15, 'yesil',               'Yeşil ışık'),

    ('pothole',         0, 'pothole',             'Çukur'),
    ('pothole',         1, 'manhole_cover',       'Rögar kapağı'),

    ('marking_damage',  0, 'road_marking_damage', 'Yol işareti hasarı')
ON CONFLICT (model_id, class_index) DO UPDATE SET
    class_name = EXCLUDED.class_name,
    display_name = EXCLUDED.display_name;

COMMENT ON TABLE roadvision_model_catalog IS
    'models.json ile uyumlu aktif RoadVision model envanteri.';
COMMENT ON TABLE roadvision_detection_type_catalog IS
    'Aktif 4 modeldeki toplam 20 tespit türünün referans kataloğu.';

-- ============================================================================
-- 3. RAPORLAMA GÖRÜNÜMLERİ
-- ============================================================================

CREATE OR REPLACE VIEW vw_roadvision_model_inventory AS
SELECT
    m.model_id,
    m.display_name,
    m.task,
    m.input_size,
    m.class_count AS declared_type_count,
    count(t.class_index)::INTEGER AS catalog_type_count,
    (m.class_count = count(t.class_index)) AS catalog_is_complete,
    m.active
FROM roadvision_model_catalog AS m
LEFT JOIN roadvision_detection_type_catalog AS t
    ON t.model_id = m.model_id
GROUP BY
    m.model_id,
    m.display_name,
    m.task,
    m.input_size,
    m.class_count,
    m.active;

COMMENT ON VIEW vw_roadvision_model_inventory IS
    'Model başına beklenen ve katalogda bulunan tespit türü sayıları.';

CREATE OR REPLACE VIEW vw_roadvision_detection_type_counts AS
WITH all_types AS (
    SELECT
        c.model_id,
        c.class_name,
        c.display_name,
        c.class_index,
        TRUE AS is_catalogued
    FROM roadvision_detection_type_catalog AS c

    UNION ALL

    SELECT DISTINCT
        o.model_id,
        o.class_name,
        o.class_name AS display_name,
        NULL::INTEGER AS class_index,
        FALSE AS is_catalogued
    FROM detected_objects AS o
    WHERE NOT EXISTS (
        SELECT 1
        FROM roadvision_detection_type_catalog AS c
        WHERE c.model_id = o.model_id
          AND c.class_name = o.class_name
    )
)
SELECT
    t.model_id,
    t.class_index,
    t.class_name,
    t.display_name,
    t.is_catalogued,
    count(o.id)::BIGINT AS total_count,
    count(o.id) FILTER (
        WHERE o.ts >= now() - interval '24 hours'
    )::BIGINT AS last_24h_count,
    count(o.id) FILTER (
        WHERE o.ts >= now() - interval '7 days'
    )::BIGINT AS last_7d_count,
    round(avg(o.confidence)::NUMERIC, 4) AS avg_confidence,
    min(o.ts) AS first_seen_at,
    max(o.ts) AS last_seen_at
FROM all_types AS t
LEFT JOIN detected_objects AS o
    ON o.model_id = t.model_id
   AND o.class_name = t.class_name
GROUP BY
    t.model_id,
    t.class_index,
    t.class_name,
    t.display_name,
    t.is_catalogued;

COMMENT ON VIEW vw_roadvision_detection_type_counts IS
    'Katalogdaki 20 tür ve varsa bilinmeyen türler için toplam/24 saat/7 gün sayımı.';

CREATE OR REPLACE VIEW vw_roadvision_daily_detection_counts AS
SELECT
    date_trunc('day', o.ts) AS day,
    o.model_id,
    o.class_name,
    count(*)::BIGINT AS detection_count,
    round(avg(o.confidence)::NUMERIC, 4) AS avg_confidence
FROM detected_objects AS o
GROUP BY
    date_trunc('day', o.ts),
    o.model_id,
    o.class_name;

COMMENT ON VIEW vw_roadvision_daily_detection_counts IS
    'Model ve tespit türü bazında günlük adet ve ortalama güven.';

CREATE OR REPLACE VIEW vw_roadvision_capture_summary AS
SELECT
    c.capture_id,
    c.ts,
    c.run_id,
    c.source_name,
    c.source_kind,
    c.frame_sequence,
    c.is_reprocess,
    string_agg(
        m.model_id || ' (' || m.object_count::TEXT || ')',
        ', ' ORDER BY m.model_id
    ) AS models,
    o.byte_size AS original_bytes,
    a.byte_size AS annotated_bytes,
    (o.byte_size + a.byte_size)::BIGINT AS referenced_bytes
FROM media_captures AS c
JOIN media_capture_models AS m
    ON m.capture_id = c.capture_id
JOIN media_blobs AS o
    ON o.id = c.original_media_id
JOIN media_blobs AS a
    ON a.id = c.annotated_media_id
GROUP BY
    c.capture_id,
    c.ts,
    c.run_id,
    c.source_name,
    c.source_kind,
    c.frame_sequence,
    c.is_reprocess,
    o.byte_size,
    a.byte_size;

COMMENT ON VIEW vw_roadvision_capture_summary IS
    'Capture metadata, tetikleyen modeller ve referans verilen JPEG boyutları.';

-- RoadVision uygulamasının desteklediği çekirdek migration sürümleri.
INSERT INTO schema_info (version)
VALUES (1), (2)
ON CONFLICT (version) DO NOTHING;

COMMIT;

-- ============================================================================
-- 4. HAZIR SORGU İHTİYAÇLARI
-- Bu bölümdeki SELECT'ler şema kurulumundan bağımsız olarak çalıştırılabilir.
-- ============================================================================

-- Q1: Kaç aktif model ve kaç tanımlı tespit türü var?
SELECT
    count(*) FILTER (WHERE active) AS active_model_count,
    sum(class_count) FILTER (WHERE active) AS detection_type_count
FROM roadvision_model_catalog;

-- Q2: Model başına tür sayısı ve katalog bütünlüğü.
SELECT *
FROM vw_roadvision_model_inventory
ORDER BY model_id;

-- Q3: Son 24 saatte türe göre tespit sayısı ve ortalama güven.
-- Semantic roadline için confidence NULL, area_ratio doludur.
SELECT
    model_id,
    class_name,
    display_name,
    last_24h_count,
    avg_confidence
FROM vw_roadvision_detection_type_counts
ORDER BY last_24h_count DESC, model_id, class_index NULLS LAST;

-- Q4: Belirli bir türün saatlik dağılımı (örnek: pothole).
SELECT
    date_trunc('hour', ts) AS hour,
    count(*) AS detection_count,
    round(avg(confidence)::NUMERIC, 4) AS avg_confidence
FROM detected_objects
WHERE model_id = 'pothole'
  AND class_name = 'pothole'
  AND ts >= now() - interval '24 hours'
GROUP BY date_trunc('hour', ts)
ORDER BY hour;

-- Q5: Model bazında olay, nesne ve çıkarım süresi özeti.
SELECT
    e.model_id,
    count(DISTINCT e.id) AS event_count,
    count(o.id) AS detected_object_count,
    round(avg(e.elapsed_ms)::NUMERIC, 2) AS avg_elapsed_ms,
    round(percentile_cont(0.95) WITHIN GROUP (
        ORDER BY e.elapsed_ms
    )::NUMERIC, 2) AS p95_elapsed_ms
FROM detection_events AS e
LEFT JOIN detected_objects AS o
    ON o.event_id = e.id
WHERE e.ts >= now() - interval '24 hours'
GROUP BY e.model_id
ORDER BY e.model_id;

-- Q6: Run bazında başlangıç/bitiş, olay ve nesne sayısı.
SELECT
    e.run_id,
    min(e.ts) AS first_event_at,
    max(e.ts) AS last_event_at,
    count(DISTINCT e.id) AS event_count,
    count(o.id) AS detected_object_count,
    count(DISTINCT e.capture_id) FILTER (
        WHERE e.capture_id IS NOT NULL
    ) AS capture_count
FROM detection_events AS e
LEFT JOIN detected_objects AS o
    ON o.event_id = e.id
GROUP BY e.run_id
ORDER BY last_event_at DESC NULLS LAST;

-- Q7: Son 50 capture ve tetikleyen model/sayılar.
SELECT *
FROM vw_roadvision_capture_summary
ORDER BY ts DESC
LIMIT 50;

-- Q8: Tespit olayı ile capture/görüntü ilişkisi.
SELECT
    e.id AS event_id,
    e.ts,
    e.run_id,
    e.model_id,
    e.object_count,
    e.capture_id,
    c.source_name,
    c.source_kind,
    c.frame_sequence,
    c.is_reprocess
FROM detection_events AS e
LEFT JOIN media_captures AS c
    ON c.capture_id = e.capture_id
WHERE e.ts >= now() - interval '24 hours'
ORDER BY e.ts DESC;

-- Q9: Payload capture_id taşıdığı halde henüz yazılmamış veya prune edilmiş medya.
SELECT
    e.id AS event_id,
    e.ts,
    e.model_id,
    e.capture_id
FROM detection_events AS e
LEFT JOIN media_captures AS c
    ON c.capture_id = e.capture_id
WHERE e.capture_id IS NOT NULL
  AND c.capture_id IS NULL
ORDER BY e.ts DESC;

-- Q10: Semantic yol çizgisi alan oranı özeti.
SELECT
    date_trunc('hour', ts) AS hour,
    count(*) AS frame_count,
    round(avg(area_ratio)::NUMERIC, 4) AS avg_area_ratio,
    round(max(area_ratio)::NUMERIC, 4) AS max_area_ratio
FROM detected_objects
WHERE model_id = 'roadline'
  AND class_name = 'roadline'
  AND ts >= now() - interval '24 hours'
GROUP BY date_trunc('hour', ts)
ORDER BY hour;

-- Q11: Medya depolama kullanımı (tekilleştirilmiş gerçek blob toplamı).
SELECT
    count(*) AS blob_count,
    pg_size_pretty(coalesce(sum(byte_size), 0)::BIGINT) AS jpeg_payload_size,
    pg_size_pretty(pg_total_relation_size('media_blobs')) AS table_total_size
FROM media_blobs;

-- Q12: Saklama süresi dolmuş capture adayları (silmez).
SELECT
    capture_id,
    ts,
    source_name,
    source_kind
FROM media_captures
WHERE ts < now() - interval '30 days'
ORDER BY ts;

-- Q13: Son hata ve uyarılar.
SELECT
    ts,
    level,
    category,
    model_id,
    message,
    payload
FROM log_records
WHERE level IN ('warning', 'error')
ORDER BY ts DESC
LIMIT 100;

-- Q14: Katalogda bulunmayan, model çıktısından yeni gelmiş türler.
SELECT DISTINCT
    o.model_id,
    o.class_name
FROM detected_objects AS o
LEFT JOIN roadvision_detection_type_catalog AS c
    ON c.model_id = o.model_id
   AND c.class_name = o.class_name
WHERE c.model_id IS NULL
ORDER BY o.model_id, o.class_name;
