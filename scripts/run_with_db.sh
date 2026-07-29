#!/bin/sh
set -eu

if [ ! -f .env ]; then
    echo ".env bulunamadı; önce 'cp .env.example .env' çalıştırın." >&2
    exit 1
fi

set -a
. ./.env
set +a

python_bin=python3
if [ -x .venv/bin/python ]; then
    python_bin=.venv/bin/python
fi

exec "$python_bin" app.py "$@"
