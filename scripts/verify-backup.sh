#!/bin/sh
set -eu

NP_SITE_ROOT=${NP_SITE_ROOT:-/srv/np-site}
AGE_IDENTITY_FILE=${AGE_IDENTITY_FILE:-"$NP_SITE_ROOT/secrets/backup-age-identity.txt"}

umask 077

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

need_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

validate_tar_archive() {
    archive=$1

    python3 -c 'import posixpath
import sys
import tarfile

archive = sys.argv[1]

with tarfile.open(archive) as tar:
    for member in tar.getmembers():
        name = member.name
        normalized_name = posixpath.normpath(name)

        if (
            not name
            or name.startswith("/")
            or normalized_name == ".."
            or normalized_name.startswith("../")
            or "/../" in normalized_name
        ):
            raise SystemExit(f"unsafe tar member path: {name!r}")

        if member.isdev():
            raise SystemExit(f"unsafe tar device member: {name!r}")

        if member.issym() or member.islnk():
            link_name = member.linkname
            normalized_target = posixpath.normpath(
                posixpath.join(posixpath.dirname(normalized_name), link_name)
            )

            if (
                not link_name
                or link_name.startswith("/")
                or normalized_target == ".."
                or normalized_target.startswith("../")
                or "/../" in normalized_target
            ):
                raise SystemExit(f"unsafe tar link target: {name!r}")
' "$archive"
}

if [ "$#" -ne 1 ]; then
    fail "usage: $0 /path/to/np-site-YYYYmmddTHHMMSSZ.tar.age"
fi

BACKUP_FILE=$1

need_command age
need_command python3
need_command tar

[ -f "$BACKUP_FILE" ] || fail "backup file not found: $BACKUP_FILE"
[ -f "$AGE_IDENTITY_FILE" ] || fail "age identity file not found: $AGE_IDENTITY_FILE"

TMP_DIR=$(mktemp -d)

cleanup() {
    rm -rf "$TMP_DIR"
}

trap cleanup EXIT INT TERM

age -d -i "$AGE_IDENTITY_FILE" -o "$TMP_DIR/backup.tar" "$BACKUP_FILE"
validate_tar_archive "$TMP_DIR/backup.tar"
tar -tf "$TMP_DIR/backup.tar" >/dev/null

mkdir -p "$TMP_DIR/payload"
tar -C "$TMP_DIR/payload" -xf "$TMP_DIR/backup.tar"

[ -f "$TMP_DIR/payload/manifest.txt" ] || fail "manifest.txt is missing"
[ -f "$TMP_DIR/payload/db/db.sqlite3" ] || fail "SQLite backup is missing"
[ -f "$TMP_DIR/payload/config/.env" ] || fail ".env backup is missing"

python3 -c 'import sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
try:
    result = connection.execute("PRAGMA integrity_check").fetchone()[0]
finally:
    connection.close()
if result != "ok":
    raise SystemExit("SQLite integrity_check failed")
' "$TMP_DIR/payload/db/db.sqlite3"

printf 'Backup verified: %s\n' "$BACKUP_FILE"
