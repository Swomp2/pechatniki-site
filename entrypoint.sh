#!/bin/sh
set -eu

: "${DJANGO_SQLITE_PATH:=/app/data/db.sqlite3}"
export DJANGO_SQLITE_PATH

if [ "${1:-}" = "gunicorn" ]; then
    python manage.py check --deploy --fail-level ERROR

    if [ "${DJANGO_COLLECTSTATIC:-0}" = "1" ]; then
        python manage.py collectstatic --noinput
    fi

    if [ "${DJANGO_MIGRATE:-0}" = "1" ]; then
        python manage.py migrate --noinput
    fi
fi

exec "$@"
