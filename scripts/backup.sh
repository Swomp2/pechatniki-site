#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
ENV_FILE="$PROJECT_DIR/.env"

read_env_value() {
    key=$1

    if [ ! -f "$ENV_FILE" ]; then
        return 0
    fi

    awk -v key="$key" '
        index($0, key "=") == 1 {
            sub("^[^=]*=", "")
            print
            exit
        }
    ' "$ENV_FILE"
}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

need_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

copy_file_if_exists() {
    source_file=$1
    target_file=$2

    if [ -f "$source_file" ]; then
        mkdir -p "$(dirname -- "$target_file")"
        cp -p "$source_file" "$target_file"
    fi
}

copy_dir_if_exists() {
    source_dir=$1
    target_dir=$2

    mkdir -p "$target_dir"

    if [ -d "$source_dir" ]; then
        (cd "$source_dir" && tar -cf - .) | (cd "$target_dir" && tar -xf -)
    fi
}

cleanup() {
    status=$?

    if [ -n "${DB_SNAPSHOT_NAME:-}" ]; then
        rm -f "$NP_SITE_ROOT/data/$DB_SNAPSHOT_NAME"
    fi

    if [ -n "${TMP_DIR:-}" ]; then
        rm -rf "$TMP_DIR"
    fi

    if [ -d "$LOCK_DIR" ]; then
        rmdir "$LOCK_DIR"
    fi

    exit "$status"
}

need_command age
need_command docker
need_command tar

if [ -z "${NP_SITE_ROOT+x}" ]; then
    NP_SITE_ROOT=$(read_env_value NP_SITE_ROOT)
fi

NP_SITE_ROOT=${NP_SITE_ROOT:-/srv/np-site}

if [ -z "${BACKUP_RETENTION_DAYS+x}" ]; then
    BACKUP_RETENTION_DAYS=$(read_env_value BACKUP_RETENTION_DAYS)
fi

BACKUP_RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-30}
BACKUP_DIR=${BACKUP_DIR:-"$NP_SITE_ROOT/backups"}
AGE_RECIPIENT_FILE=${AGE_RECIPIENT_FILE:-"$NP_SITE_ROOT/secrets/backup-age-recipient.txt"}
LOCK_DIR=${BACKUP_LOCK_DIR:-"$BACKUP_DIR/.backup.lock"}

umask 077
mkdir -p "$BACKUP_DIR"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    fail "another backup process is already running"
fi

TMP_DIR=$(mktemp -d "$BACKUP_DIR/.tmp.XXXXXX")
trap cleanup EXIT INT TERM

[ -f "$AGE_RECIPIENT_FILE" ] || fail "age recipient file not found: $AGE_RECIPIENT_FILE"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
PAYLOAD_DIR="$TMP_DIR/payload"
ARCHIVE_NAME="np-site-$STAMP.tar"
DB_SNAPSHOT_NAME=".backup-$STAMP.sqlite3"

mkdir -p "$PAYLOAD_DIR/db" "$PAYLOAD_DIR/config"

docker compose -f "$PROJECT_DIR/docker-compose.yml" exec -T web python -c 'import os, sqlite3, sys
source_path = os.environ.get("DJANGO_SQLITE_PATH", "/app/data/db.sqlite3")
target_path = "/app/data/" + sys.argv[1]
source = sqlite3.connect(source_path)
target = sqlite3.connect(target_path)
try:
    source.backup(target)
finally:
    target.close()
    source.close()
' "$DB_SNAPSHOT_NAME"

cp -p "$NP_SITE_ROOT/data/$DB_SNAPSHOT_NAME" "$PAYLOAD_DIR/db/db.sqlite3"
rm -f "$NP_SITE_ROOT/data/$DB_SNAPSHOT_NAME"
DB_SNAPSHOT_NAME=

copy_dir_if_exists "$NP_SITE_ROOT/media" "$PAYLOAD_DIR/media"
copy_dir_if_exists "$NP_SITE_ROOT/letsencrypt" "$PAYLOAD_DIR/letsencrypt"
copy_dir_if_exists "$NP_SITE_ROOT/certbot" "$PAYLOAD_DIR/certbot"
copy_dir_if_exists "$NP_SITE_ROOT/secrets" "$PAYLOAD_DIR/secrets"

copy_file_if_exists "$PROJECT_DIR/.env" "$PAYLOAD_DIR/config/.env"
copy_file_if_exists "$PROJECT_DIR/docker-compose.yml" "$PAYLOAD_DIR/config/docker-compose.yml"
copy_file_if_exists "$PROJECT_DIR/Dockerfile" "$PAYLOAD_DIR/config/Dockerfile"
copy_file_if_exists "$PROJECT_DIR/env.example" "$PAYLOAD_DIR/config/env.example"

copy_dir_if_exists "$PROJECT_DIR/deploy" "$PAYLOAD_DIR/config/deploy"
copy_dir_if_exists "$PROJECT_DIR/scripts" "$PAYLOAD_DIR/config/scripts"

{
    printf 'created_at_utc=%s\n' "$STAMP"
    printf 'project_dir=%s\n' "$PROJECT_DIR"
    printf 'site_root=%s\n' "$NP_SITE_ROOT"
    printf 'database=sqlite\n'
    git -C "$PROJECT_DIR" rev-parse HEAD 2>/dev/null | sed 's/^/git_commit=/'
} > "$PAYLOAD_DIR/manifest.txt"

tar -C "$PAYLOAD_DIR" -cf "$TMP_DIR/$ARCHIVE_NAME" .

FINAL_BACKUP="$BACKUP_DIR/$ARCHIVE_NAME.age"
TMP_BACKUP="$FINAL_BACKUP.tmp"

age -R "$AGE_RECIPIENT_FILE" -o "$TMP_BACKUP" "$TMP_DIR/$ARCHIVE_NAME"
"$SCRIPT_DIR/verify-backup.sh" "$TMP_BACKUP" >/dev/null
mv "$TMP_BACKUP" "$FINAL_BACKUP"

find "$BACKUP_DIR" -type f -name 'np-site-*.tar.age' -mtime +"$BACKUP_RETENTION_DAYS" -delete

printf 'Backup created: %s\n' "$FINAL_BACKUP"
