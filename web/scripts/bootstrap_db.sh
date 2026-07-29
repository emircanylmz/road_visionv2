#!/usr/bin/env bash
# Var olan (dolu) PostgreSQL kurulumuna web rolünü ve webapp şemasını kurar.
# Tekrar çalıştırılabilir. Repo kökündeki .env dosyasından
# ROADVISION_WEB_PASSWORD ve (varsa) ROADVISION_DB_DSN okunur.
#
# Parola komut satırına DSN içinde yazılmaz; psql değişkeni olarak
# aktarılır (mevcut scripts/*.py --dsn uyarı disipliniyle aynı gerekçe).
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo kökü

if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

: "${ROADVISION_WEB_PASSWORD:?.env içinde ROADVISION_WEB_PASSWORD tanımlayın}"

SQL_FILE=web/db/bootstrap.sql

if [ -n "${ROADVISION_DB_DSN:-}" ] && command -v psql >/dev/null 2>&1; then
    # Masaüstünün sahip DSN'i ile doğrudan bağlan.
    psql "$ROADVISION_DB_DSN" -v ON_ERROR_STOP=1 \
         -v web_password="$ROADVISION_WEB_PASSWORD" -f "$SQL_FILE"
else
    # Yerel psql yoksa compose içindeki sunucu üzerinden uygula; SQL dosyası
    # compose tarafından /web-db/bootstrap.sql olarak bağlıdır.
    docker compose exec -T postgres psql -v ON_ERROR_STOP=1 \
        -U "${POSTGRES_USER:-roadvision}" -d "${POSTGRES_DB:-roadvision}" \
        -v web_password="$ROADVISION_WEB_PASSWORD" -f /web-db/bootstrap.sql
fi

echo "Web DB temeli uygulandı (rol: roadvision_web, şema: webapp)."
