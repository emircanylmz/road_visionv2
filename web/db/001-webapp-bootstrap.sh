#!/usr/bin/env bash
# Yeni (boş) PostgreSQL volume'ünde initdb sırasında web temelini kurar.
# compose.yaml bu dosyayı /docker-entrypoint-initdb.d/003-... olarak,
# bootstrap.sql'i de /web-db/bootstrap.sql olarak bağlar. Var olan
# volume'lerde initdb betikleri çalışmaz; onlar için
# web/scripts/bootstrap_db.sh kullanılır (WEB_PLANI.md §9 Faz 0).
set -euo pipefail

if [ -z "${ROADVISION_WEB_PASSWORD:-}" ]; then
    echo "003-webapp-bootstrap: ROADVISION_WEB_PASSWORD tanımsız," \
         "web rolü kurulumu atlandı (sonradan web/scripts/bootstrap_db.sh" \
         "ile kurulabilir)." >&2
    exit 0
fi

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v web_password="$ROADVISION_WEB_PASSWORD" \
     -f /web-db/bootstrap.sql
