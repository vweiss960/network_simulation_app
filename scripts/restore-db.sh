#!/usr/bin/env bash
# Phanero DB restore — replaces the live SQLite DB with a backup taken by
# scripts/upgrade.sh. Stops the backend so the file isn't open during copy,
# then brings it back up.
#
# Usage:
#   scripts/restore-db.sh backups/network_modeler.20260513-054404.db

set -euo pipefail

cd "$(dirname "$0")/.."

step() { printf "\033[1;36m→ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m✓ %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m! %s\033[0m\n" "$*"; }
err()  { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; }

if [ $# -lt 1 ]; then
    err "Missing backup file argument"
    echo "Usage: $0 <path/to/backup.db>"
    echo
    echo "Available backups:"
    ls -1t backups/network_modeler.*.db 2>/dev/null | head -10 | sed 's/^/  /' || echo "  (none found in ./backups)"
    exit 2
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
    err "Backup file not found: $BACKUP_FILE"
    exit 1
fi

# Sanity-check that it looks like a SQLite file
if ! head -c 16 "$BACKUP_FILE" | grep -q "SQLite format 3"; then
    err "$BACKUP_FILE does not look like a SQLite database"
    exit 1
fi

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "About to restore:"
echo "    File:  $BACKUP_FILE  ($SIZE)"
echo "    Into:  backend container → /app/data/network_modeler.db"
echo
read -r -p "Continue? [y/N] " ans
case "$ans" in
    y|Y|yes|YES) ;;
    *) warn "Aborted."; exit 0 ;;
esac

# Safety net: snapshot the current DB before overwriting it.
SAFETY="backups/network_modeler.pre-restore.$(date +%Y%m%d-%H%M%S).db"
mkdir -p backups
BACKEND_CID=$(docker compose ps -q backend 2>/dev/null || true)
if [ -n "$BACKEND_CID" ] && docker inspect -f '{{.State.Running}}' "$BACKEND_CID" 2>/dev/null | grep -q true; then
    step "Snapshotting current DB → $SAFETY"
    docker compose exec -T backend sqlite3 /app/data/network_modeler.db \
        ".backup /app/data/_phanero_prerestore.db" >/dev/null
    docker cp "$BACKEND_CID:/app/data/_phanero_prerestore.db" "$SAFETY"
    docker compose exec -T backend rm -f /app/data/_phanero_prerestore.db
    ok "Pre-restore snapshot saved"
else
    warn "Backend not running — skipping pre-restore snapshot"
fi

step "Stopping backend"
docker compose stop backend

# After stop, the container still exists; copy into it, then start.
BACKEND_CID=$(docker compose ps -q backend)
if [ -z "$BACKEND_CID" ]; then
    err "Could not find backend container after stop"
    exit 1
fi

step "Copying $BACKUP_FILE → backend:/app/data/network_modeler.db"
docker cp "$BACKUP_FILE" "$BACKEND_CID:/app/data/network_modeler.db"

step "Starting backend"
docker compose start backend

step "Waiting for backend to respond..."
for i in $(seq 1 60); do
    if curl -sk --max-time 2 https://localhost:3000/api/openapi.json -o /dev/null 2>/dev/null; then
        ok "Backend healthy after ${i}s"
        break
    fi
    sleep 1
    if [ "$i" -eq 60 ]; then
        err "Backend did not respond within 60s — check 'docker compose logs backend'"
        exit 1
    fi
done

echo
ok "Restore complete."
echo "    Restored:           $BACKUP_FILE"
if [ -f "$SAFETY" ]; then
    echo "    Pre-restore safety: $SAFETY"
    echo "    (Roll forward with: scripts/restore-db.sh $SAFETY)"
fi
