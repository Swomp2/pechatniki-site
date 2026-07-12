#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

NP_SITE_ROOT=${NP_SITE_ROOT:-/srv/np-site}
AGE_IDENTITY_FILE=${AGE_IDENTITY_FILE:-"$NP_SITE_ROOT/secrets/backup-age-identity.txt"}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

need_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

restore_dir() {
    source_dir=$1
    target_dir=$2

    mkdir -p "$target_dir"

    if [ -d "$source_dir" ]; then
        (cd "$source_dir" && tar -cf - .) | (cd "$target_dir" && tar -xf -)
    fi
}

if [ "$#" -ne 2 ] || [ "$2" != "--yes" ]; then
    fail "usage: $0 /path/to/np-site-YYYYmmddTHHMMSSZ.tar.age --yes"
fi

BACKUP_FILE=$1

need_command age
need_command docker
need_command python3
need_command tar

[ -f "$BACKUP_FILE" ] || fail "backup file not found: $BACKUP_FILE"
[ -f "$AGE_IDENTITY_FILE" ] || fail "age identity file not found: $AGE_IDENTITY_FILE"

"$SCRIPT_DIR/verify-backup.sh" "$BACKUP_FILE" >/dev/null

TMP_DIR=$(mktemp -d)

cleanup() {
    rm -rf "$TMP_DIR"
}

trap cleanup EXIT INT TERM

age -d -i "$AGE_IDENTITY_FILE" -o "$TMP_DIR/backup.tar" "$BACKUP_FILE"
mkdir -p "$TMP_DIR/payload"
tar -C "$TMP_DIR/payload" -xf "$TMP_DIR/backup.tar"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
SAFETY_DIR="$NP_SITE_ROOT/restore-safety-$STAMP"

docker compose -f "$PROJECT_DIR/docker-compose.yml" down

mkdir -p "$SAFETY_DIR"

for name in data media staticfiles letsencrypt certbot secrets; do
    if [ -e "$NP_SITE_ROOT/$name" ]; then
        mv "$NP_SITE_ROOT/$name" "$SAFETY_DIR/$name"
    fi
done

if [ -f "$PROJECT_DIR/.env" ]; then
    cp -p "$PROJECT_DIR/.env" "$SAFETY_DIR/.env"
fi

mkdir -p "$NP_SITE_ROOT/data" "$NP_SITE_ROOT/media" "$NP_SITE_ROOT/staticfiles"
mkdir -p "$NP_SITE_ROOT/letsencrypt" "$NP_SITE_ROOT/certbot" "$NP_SITE_ROOT/secrets"

cp -p "$TMP_DIR/payload/db/db.sqlite3" "$NP_SITE_ROOT/data/db.sqlite3"
restore_dir "$TMP_DIR/payload/media" "$NP_SITE_ROOT/media"
restore_dir "$TMP_DIR/payload/letsencrypt" "$NP_SITE_ROOT/letsencrypt"
restore_dir "$TMP_DIR/payload/certbot" "$NP_SITE_ROOT/certbot"
restore_dir "$TMP_DIR/payload/secrets" "$NP_SITE_ROOT/secrets"
copy_env="$TMP_DIR/payload/config/.env"

if [ -f "$copy_env" ]; then
    cp -p "$copy_env" "$PROJECT_DIR/.env"
fi

chown -R 10001:10001 "$NP_SITE_ROOT/data" "$NP_SITE_ROOT/media" "$NP_SITE_ROOT/staticfiles"
chmod 700 "$NP_SITE_ROOT/secrets"
find "$NP_SITE_ROOT/secrets" -type f -exec chmod 600 {} \;

docker compose -f "$PROJECT_DIR/docker-compose.yml" up -d

printf 'Restore completed from: %s\n' "$BACKUP_FILE"
printf 'Previous files were moved to: %s\n' "$SAFETY_DIR"
