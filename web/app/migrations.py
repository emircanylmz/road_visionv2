"""webapp şeması için sürüm-kapılı migration runner'ı.

Masaüstündeki ``roadvision/db.py ensure_schema`` deseninin web karşılığıdır
(bkz. WEB_PLANI.md §4.7):

* ``webapp.schema_info`` sürüm tablosu,
* ``pg_advisory_xact_lock`` altında tek transaction,
* sıra atlaması yasak, tekrar çalıştırılabilir migration listesi.

Modül, testlerin torch/psycopg kurulu olmayan ortamda da çalışabilmesi için
import anında hiçbir üçüncü parti pakete bağlanmaz; ``psycopg`` yalnız
``run_migrations`` çağrıldığında yüklenir.
"""

from __future__ import annotations

from typing import Any, Sequence

# Masaüstü migration kilidi 1385428466, bootstrap 1385428468'dir; web
# migration'ları çakışmamak için ayrık sabit kullanır (WEB_PLANI.md §4.1).
WEBAPP_ADVISORY_LOCK = 1385428467

SCHEMA_MISSING_HINT = (
    "webapp şeması bulunamadı. Önce DB temelini kurun: "
    "web/scripts/bootstrap_db.sh (ayrıntı: WEB_PLANI.md §9 Faz 0)."
)

Migration = tuple[int, str]

# v1 — kimlik ve denetim (WEB_PLANI.md §4.2, Faz 1).
# sessions.csrf_token: oturuma bağlı double-submit CSRF belirteci (§8);
# sessions.last_seen_at: 30 dk hareketsizlik süresi için son etkinlik izi.
_MIGRATION_V1_KIMLIK = """\
CREATE TABLE webapp.users (
    user_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email         TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    full_name     TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'member'
                  CHECK (role IN ('member', 'admin')),
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'approved', 'rejected', 'disabled')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at   TIMESTAMPTZ,
    approved_by   BIGINT REFERENCES webapp.users(user_id)
);

CREATE UNIQUE INDEX users_email_lower_uq ON webapp.users (lower(email));

CREATE TABLE webapp.sessions (
    session_id   UUID PRIMARY KEY,
    user_id      BIGINT NOT NULL
                 REFERENCES webapp.users(user_id) ON DELETE CASCADE,
    csrf_token   TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip           INET,
    user_agent   TEXT
);

CREATE INDEX sessions_user_idx ON webapp.sessions (user_id, expires_at);

CREATE TABLE webapp.admin_audit (
    audit_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id   BIGINT NOT NULL REFERENCES webapp.users(user_id),
    action     TEXT NOT NULL,
    target     TEXT NOT NULL,
    detail     JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX admin_audit_created_idx ON webapp.admin_audit (created_at);
"""

# v2 — doğrulama kararları (WEB_PLANI.md §4.3; tablo Faz 3'te açılır,
# yazım uçları Faz 4'tedir). Satır yokluğu = doğrulanmadı; object_id
# public.detected_objects.id'ye FK'sız işaret eder (retention bağımsızlığı,
# bkz. §2/2). corrected_payload CHECK'i: yalnız 'corrected' kararı düzeltme
# taşır ve en az bir düzeltme taşımak zorundadır.
_MIGRATION_V2_DOGRULAMA = """\
CREATE TABLE webapp.detection_reviews (
    object_id         BIGINT PRIMARY KEY,
    verdict           TEXT NOT NULL
                      CHECK (verdict IN ('correct', 'corrected', 'wrong')),
    corrected_bbox    REAL[]
                      CHECK (corrected_bbox IS NULL
                             OR array_length(corrected_bbox, 1) = 4),
    corrected_type_id INTEGER,
    reviewer_id       BIGINT NOT NULL REFERENCES webapp.users(user_id),
    reviewed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    note              TEXT,
    CONSTRAINT corrected_payload CHECK (
        (verdict = 'corrected')
        = (corrected_bbox IS NOT NULL OR corrected_type_id IS NOT NULL)
    )
);

CREATE INDEX detection_reviews_reviewed_idx
    ON webapp.detection_reviews (reviewed_at);
"""

# v3 — dataset katmanı (WEB_PLANI.md §4.4–4.5, Faz 4). dataset_media
# copy-on-verify deposudur (retention'dan bağımsız); dataset_samples karar ×
# model bildirimsel bölümlemesiyle "tür ve doğruluğa göre ayrı tablolar"
# gereksinimini karşılar (2 karar grubu × 4 model = 8 yaprak). Beşinci model
# ancak bilinçli bir migration ile eklenir. Trigger, §4.3'te söz verilen
# corrected_type_id "aynı model sözlüğü" kuralını DB seviyesine bağlar.
_MIGRATION_V3_DATASET = """\
CREATE TABLE webapp.dataset_media (
    sha256    TEXT PRIMARY KEY,
    bytes     BYTEA NOT NULL,
    width     INTEGER,
    height    INTEGER,
    byte_size INTEGER NOT NULL
);

CREATE TABLE webapp.dataset_samples (
    sample_id        BIGINT GENERATED ALWAYS AS IDENTITY,
    object_id        BIGINT NOT NULL,
    verdict          TEXT NOT NULL,
    model_id         TEXT NOT NULL,
    type_id          INTEGER NOT NULL,
    class_name       TEXT NOT NULL,
    confidence       REAL,
    bbox             REAL[],
    area_ratio       REAL,
    final_type_id    INTEGER NOT NULL,
    final_class_name TEXT NOT NULL,
    final_bbox       REAL[],
    frame_w          INTEGER,
    frame_h          INTEGER,
    detected_at      TIMESTAMPTZ NOT NULL,
    run_id           BIGINT,
    capture_id       UUID,
    original_sha     TEXT REFERENCES webapp.dataset_media(sha256),
    annotated_sha    TEXT REFERENCES webapp.dataset_media(sha256),
    reviewed_at      TIMESTAMPTZ NOT NULL,
    reviewer_id      BIGINT NOT NULL,
    PRIMARY KEY (verdict, model_id, sample_id)
) PARTITION BY LIST (verdict);

CREATE TABLE webapp.dataset_positive PARTITION OF webapp.dataset_samples
    FOR VALUES IN ('correct', 'corrected') PARTITION BY LIST (model_id);
CREATE TABLE webapp.dataset_wrong PARTITION OF webapp.dataset_samples
    FOR VALUES IN ('wrong') PARTITION BY LIST (model_id);

CREATE TABLE webapp.ds_positive_roadline
    PARTITION OF webapp.dataset_positive FOR VALUES IN ('roadline');
CREATE TABLE webapp.ds_positive_traffic_sign
    PARTITION OF webapp.dataset_positive FOR VALUES IN ('traffic_sign');
CREATE TABLE webapp.ds_positive_pothole
    PARTITION OF webapp.dataset_positive FOR VALUES IN ('pothole');
CREATE TABLE webapp.ds_positive_marking_damage
    PARTITION OF webapp.dataset_positive FOR VALUES IN ('marking_damage');
CREATE TABLE webapp.ds_wrong_roadline
    PARTITION OF webapp.dataset_wrong FOR VALUES IN ('roadline');
CREATE TABLE webapp.ds_wrong_traffic_sign
    PARTITION OF webapp.dataset_wrong FOR VALUES IN ('traffic_sign');
CREATE TABLE webapp.ds_wrong_pothole
    PARTITION OF webapp.dataset_wrong FOR VALUES IN ('pothole');
CREATE TABLE webapp.ds_wrong_marking_damage
    PARTITION OF webapp.dataset_wrong FOR VALUES IN ('marking_damage');

CREATE INDEX dataset_samples_type_ts_idx
    ON webapp.dataset_samples (type_id, detected_at);
CREATE INDEX dataset_samples_object_idx
    ON webapp.dataset_samples (object_id);

-- §4.3: corrected_type_id tespitin geldiği modelin sözlüğünden olmalı.
-- API katmanı aynı kuralı önden doğrular; trigger son savunma hattıdır.
CREATE FUNCTION webapp.detection_reviews_corrected_type_ck()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
    IF NEW.verdict = 'corrected' AND NEW.corrected_type_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1
            FROM public.detected_objects AS o
            JOIN public.detection_types AS t
                ON t.model_id = o.model_id
            WHERE o.id = NEW.object_id
              AND t.type_id = NEW.corrected_type_id
        ) THEN
            RAISE EXCEPTION
                'corrected_type_id % tespitin modeline ait değil',
                NEW.corrected_type_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$;

CREATE TRIGGER detection_reviews_corrected_type_trg
    BEFORE INSERT OR UPDATE ON webapp.detection_reviews
    FOR EACH ROW
    EXECUTE FUNCTION webapp.detection_reviews_corrected_type_ck();
"""

# v4 — export işleri (WEB_PLANI.md §6, Faz 5). Zip çıktısı da PostgreSQL'de
# saklanır: konteynerler geçicidir ve servisin paylaştığı tek durum DB'dir
# (§2 "tek temas" ilkesi). BYTEA satırı indirme ucundan sunulur; iş durumu
# pending → running → done/failed akışıyla izlenir.
_MIGRATION_V4_EXPORT = """\
CREATE TABLE webapp.export_jobs (
    job_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    requested_by     BIGINT NOT NULL REFERENCES webapp.users(user_id),
    model_id         TEXT NOT NULL,
    verdict_scope    TEXT NOT NULL
        CHECK (verdict_scope IN ('positive', 'wrong')),
    status           TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'done', 'failed')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    sample_count     INTEGER,
    image_count      INTEGER,
    skipped_no_image INTEGER,
    skipped_no_bbox  INTEGER,
    byte_size        BIGINT,
    zip_bytes        BYTEA,
    error            TEXT
);

CREATE INDEX export_jobs_created_idx
    ON webapp.export_jobs (created_at DESC);
CREATE INDEX export_jobs_status_idx
    ON webapp.export_jobs (status);
CREATE UNIQUE INDEX export_jobs_active_uq
    ON webapp.export_jobs (model_id, verdict_scope)
    WHERE status IN ('pending', 'running');
"""

MIGRATIONS: tuple[Migration, ...] = (
    (1, _MIGRATION_V1_KIMLIK),
    (2, _MIGRATION_V2_DOGRULAMA),
    (3, _MIGRATION_V3_DATASET),
    (4, _MIGRATION_V4_EXPORT),
)

CURRENT_VERSION: int = MIGRATIONS[-1][0] if MIGRATIONS else 0

_VERSION_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS webapp.schema_info (
    version    INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)"""


def ensure_webapp_schema(
    conn: Any,
    migrations: Sequence[Migration] = MIGRATIONS,
) -> int:
    """Bekleyen webapp migration'larını uygular ve son sürümü döndürür.

    Bağlantı, ``transaction()`` ve ``cursor()`` bağlam yöneticilerini sunan
    bir psycopg bağlantısı (veya testlerdeki eşdeğer fake) olmalıdır.
    Fonksiyon transaction yönetimini kendi üstlenir; açık bir transaction
    içinden çağrılmamalıdır.
    """

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(%s)", (WEBAPP_ADVISORY_LOCK,)
            )
            cur.execute(
                "SELECT 1 FROM pg_namespace WHERE nspname = 'webapp'"
            )
            if cur.fetchone() is None:
                raise RuntimeError(SCHEMA_MISSING_HINT)
            cur.execute(_VERSION_TABLE_SQL)
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) FROM webapp.schema_info"
            )
            row = cur.fetchone()
            current = int(row[0]) if row else 0
            for version, sql in migrations:
                if version <= current:
                    continue
                if version != current + 1:
                    raise RuntimeError(
                        "webapp migration sırası bozuk: mevcut sürüm "
                        f"{current}, sıradaki dosya {version}. Aradaki "
                        "migration eksik; MIGRATIONS listesini kontrol edin."
                    )
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO webapp.schema_info (version) VALUES (%s)",
                    (version,),
                )
                current = version
            return current


def run_migrations(dsn: str) -> int:
    """DSN ile kısa ömürlü bağlantı açar ve migration'ları uygular.

    Uygulama açılışında (``lifespan``) bir kez, ``asyncio.to_thread``
    içinden çağrılır; birden çok API kopyası aynı anda açılsa da advisory
    lock tek uygulayıcı garantisi verir.
    """

    import psycopg

    with psycopg.connect(dsn) as conn:
        return ensure_webapp_schema(conn)
