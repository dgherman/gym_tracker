#!/usr/bin/env bash
#
# Restore the latest production gym_tracker S3 backup into the local Docker DB.
#
# What it does:
#   1. Finds the newest gym_tracker-*.sql.gz in the S3 backups bucket
#   2. Downloads and unpacks it to /tmp
#   3. Drops + recreates the local `gym_tracker` database (container: gym_tracker_local)
#   4. Loads the dump and runs `alembic upgrade head`
#
# Usage:
#   scripts/restore_prod_backup.sh        # asks for confirmation before dropping local DB
#   scripts/restore_prod_backup.sh -y     # no confirmation
#
set -euo pipefail

S3_PREFIX="s3://backups705818322820/rackerd/databases/"
CONTAINER="gym_tracker_local"
DB_NAME="gym_tracker"
DB_USER="gym"
ROOT_PW="rootpass"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

ASSUME_YES=0
[[ "${1:-}" == "-y" || "${1:-}" == "--yes" ]] && ASSUME_YES=1

# --- preflight ---------------------------------------------------------------
command -v aws >/dev/null || { echo "ERROR: aws CLI not found" >&2; exit 1; }
docker ps --format '{{.Names}}' | grep -qx "$CONTAINER" \
    || { echo "ERROR: container '$CONTAINER' not running" >&2; exit 1; }

# --- find latest backup ------------------------------------------------------
LATEST=$(aws s3 ls "$S3_PREFIX" \
    | awk '{print $4}' \
    | grep -E "^${DB_NAME}-[0-9]{4}-[0-9]{2}-[0-9]{2}_.*\.sql\.gz$" \
    | sort \
    | tail -1)
[[ -n "$LATEST" ]] || { echo "ERROR: no ${DB_NAME} backups found in $S3_PREFIX" >&2; exit 1; }
echo "Latest backup: $LATEST"

# --- confirm -----------------------------------------------------------------
if [[ $ASSUME_YES -ne 1 ]]; then
    read -r -p "This will DROP the local '$DB_NAME' database and replace it. Continue? [y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }
fi

# --- download + unpack -------------------------------------------------------
GZ="/tmp/$LATEST"
SQL="${GZ%.gz}"
aws s3 cp "${S3_PREFIX}${LATEST}" "$GZ"
gunzip -f "$GZ"
echo "Dump unpacked to $SQL"

# --- recreate DB and load dump -----------------------------------------------
docker exec -i "$CONTAINER" mysql -uroot -p"$ROOT_PW" <<EOF
DROP DATABASE IF EXISTS $DB_NAME;
CREATE DATABASE $DB_NAME;
GRANT ALL ON $DB_NAME.* TO '$DB_USER'@'%';
FLUSH PRIVILEGES;
EOF
docker exec -i "$CONTAINER" mysql -uroot -p"$ROOT_PW" "$DB_NAME" < "$SQL"
echo "Dump loaded into '$DB_NAME'."

# --- migrate to local head ---------------------------------------------------
cd "$REPO_ROOT"
.venv/bin/alembic upgrade head
echo "Done. Local '$DB_NAME' now matches prod backup $LATEST + local migrations."
