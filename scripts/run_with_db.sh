#!/bin/sh
set -eu

if [ ! -f .env ]; then
    echo ".env bulunamadı; önce 'cp .env.example .env' çalıştırın." >&2
    exit 1
fi

set -a
. ./.env
set +a

exec python3 app.py "$@"
