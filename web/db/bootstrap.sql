\set ON_ERROR_STOP on

/*
RoadVision Web Paneli — Faz 0 DB temeli (RVU-0004, WEB_PLANI.md §4.1)

Bu betik masaüstü şemasının SAHİBİ rolle (compose kurulumunda POSTGRES_USER)
çalıştırılır ve tekrar çalıştırılabilir:

  - roadvision_web login rolünü oluşturur (varsa parolasını tazeler),
  - public şemasında yalnız USAGE + SELECT verir; masaüstünün İLERİDE
    oluşturacağı tablolar için ALTER DEFAULT PRIVILEGES ile SELECT bağlar,
  - webapp şemasını oluşturup sahipliğini roadvision_web'e verir.

Parola psql değişkeniyle verilir; doğrudan çağrı örneği:

  psql "$ROADVISION_DB_DSN" -v ON_ERROR_STOP=1 \
       -v web_password="$ROADVISION_WEB_PASSWORD" -f web/db/bootstrap.sql

Normal yol web/scripts/bootstrap_db.sh sarmalayıcısıdır.
*/

BEGIN;

-- Masaüstü migration kilidi 1385428466, web migration kilidi 1385428467;
-- bootstrap ayrık 1385428468 sabitini kullanır (WEB_PLANI.md §4.1 tablosu).
SELECT pg_advisory_xact_lock(1385428468);

-- psql değişkenini DO bloklarının okuyabileceği transaction-yerel GUC'a
-- taşı. \gset sonucu ekrana basmadan psql değişkenine alır; parola bootstrap
-- çıktısına veya CI günlüklerine sızmaz.
SELECT set_config(
    'roadvision.web_password',
    :'web_password',
    true
) AS _roadvision_web_password \gset

DO $$
DECLARE
    v_pwd TEXT := current_setting('roadvision.web_password', true);
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'roadvision_web') THEN
        IF v_pwd IS NULL OR v_pwd = '' THEN
            RAISE EXCEPTION
                'roadvision_web rolü yok ve parola verilmedi; '
                'psql -v web_password=... ile çalıştırın '
                '(.env: ROADVISION_WEB_PASSWORD).';
        END IF;
        EXECUTE format('CREATE ROLE roadvision_web LOGIN PASSWORD %L', v_pwd);
        RAISE NOTICE 'roadvision_web rolü oluşturuldu';
    ELSIF v_pwd IS NOT NULL AND v_pwd <> '' THEN
        -- Tekrar çalıştırmada parolayı .env ile eşitle (rotasyon yolu).
        EXECUTE format('ALTER ROLE roadvision_web PASSWORD %L', v_pwd);
        RAISE NOTICE 'roadvision_web parolası güncellendi';
    END IF;
END
$$;

DO $$
BEGIN
    EXECUTE format(
        'GRANT CONNECT ON DATABASE %I TO roadvision_web', current_database()
    );
END
$$;

-- public: yalnız okuma. REVOKE'lar PG15+ varsayılanlarını açıkça sabitler.
GRANT USAGE ON SCHEMA public TO roadvision_web;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO roadvision_web;
REVOKE CREATE ON SCHEMA public FROM roadvision_web;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON ALL TABLES IN SCHEMA public FROM roadvision_web;

-- Bu betiği çalıştıran rol masaüstü tablolarının yaratıcısıdır; onun
-- ileride oluşturacağı public tabloları da otomatik SELECT kapsamına girer.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO roadvision_web;

-- webapp: webin kendi alanı. Sahiplik devri tekrar çalıştırmada zararsızdır.
CREATE SCHEMA IF NOT EXISTS webapp;
ALTER SCHEMA webapp OWNER TO roadvision_web;

COMMIT;

\echo 'RoadVision web DB temeli hazır: rol=roadvision_web, şema=webapp'
