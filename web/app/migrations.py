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

MIGRATIONS: tuple[Migration, ...] = (
    (1, _MIGRATION_V1_KIMLIK),
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
