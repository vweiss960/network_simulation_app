#!/usr/bin/env bash
# Phanero in-place upgrade — backs up the DB, pulls latest code, rebuilds and
# recreates containers, waits for the backend to come back up.
#
# Usage:
#   scripts/upgrade.sh                  # backup + pull + rebuild
#   scripts/upgrade.sh --no-pull        # backup + rebuild (no git pull)
#   scripts/upgrade.sh --no-build       # backup + pull, skip rebuild (config-only changes)
#
# Backups land in ./backups/network_modeler.<timestamp>.db
# The newest 10 are kept; older ones are pruned.

set -euo pipefail

cd "$(dirname "$0")/.."

BACKUP_DIR="./backups"
KEEP_BACKUPS=10
DO_PULL=1
DO_BUILD=1
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

for arg in "$@"; do
    case "$arg" in
        --no-pull)  DO_PULL=0 ;;
        --no-build) DO_BUILD=0 ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -20
            exit 0 ;;
        *)
            echo "Unknown arg: $arg (try --help)"
            exit 2 ;;
    esac
done

step() { printf "\033[1;36m→ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m✓ %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m! %s\033[0m\n" "$*"; }
err()  { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; }

# -- 1. Pre-flight ----------------------------------------------------------
mkdir -p "$BACKUP_DIR" lab_state
if ! command -v docker >/dev/null; then err "docker not installed"; exit 1; fi
if ! docker compose version >/dev/null 2>&1; then err "docker compose v2 plugin required"; exit 1; fi

# -- 2. Back up the SQLite DB (online — backend can keep running) ----------
BACKEND_CID=$(docker compose ps -q backend 2>/dev/null || true)
BACKUP_FILE=""
if [ -n "$BACKEND_CID" ] && docker inspect -f '{{.State.Running}}' "$BACKEND_CID" 2>/dev/null | grep -q true; then
    BACKUP_FILE="$BACKUP_DIR/network_modeler.${TIMESTAMP}.db"
    step "Backing up DB → $BACKUP_FILE"
    # sqlite3's online backup is safe under concurrent writes
    docker compose exec -T backend sqlite3 /app/data/network_modeler.db \
        ".backup /app/data/_phanero_backup.db" >/dev/null
    docker cp "$BACKEND_CID:/app/data/_phanero_backup.db" "$BACKUP_FILE"
    docker compose exec -T backend rm -f /app/data/_phanero_backup.db
    ok "Backup written ($(du -h "$BACKUP_FILE" | cut -f1))"

    # Prune old backups
    OLD=$(ls -1t "$BACKUP_DIR"/network_modeler.*.db 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) || true)
    if [ -n "$OLD" ]; then
        N=$(echo "$OLD" | wc -l)
        echo "$OLD" | xargs -r rm -f
        warn "Pruned $N old backup(s); keeping the newest $KEEP_BACKUPS"
    fi
else
    warn "Backend not running — skipping live DB backup (volume is intact)"
fi

# -- 3. Pull latest code ----------------------------------------------------
if [ "$DO_PULL" = "1" ]; then
    step "git pull"
    BEFORE=$(git rev-parse HEAD)
    git pull --ff-only
    AFTER=$(git rev-parse HEAD)
    if [ "$BEFORE" = "$AFTER" ]; then
        warn "Already up to date — no new commits"
    else
        ok "Updated $BEFORE..$AFTER"
    fi
else
    warn "Skipping git pull (--no-pull)"
fi

# -- 4. Rebuild + recreate --------------------------------------------------
if [ "$DO_BUILD" = "1" ]; then
    step "docker compose up -d --build"
    docker compose up -d --build
else
    step "docker compose up -d (no rebuild)"
    docker compose up -d
fi

# -- 5. Wait for backend health --------------------------------------------
step "Waiting for backend to respond..."
for i in $(seq 1 90); do
    if curl -sk --max-time 2 https://localhost:3000/api/openapi.json -o /dev/null 2>/dev/null; then
        ok "Backend healthy after ${i}s"
        break
    fi
    sleep 1
    if [ "$i" -eq 90 ]; then
        err "Backend did not respond within 90s"
        warn "If something went wrong, restore with: scripts/restore-db.sh $BACKUP_FILE"
        exit 1
    fi
done

# -- 6. Done ----------------------------------------------------------------
echo
ok "Upgrade complete."
if [ -n "$BACKUP_FILE" ]; then
    echo "    Backup:  $BACKUP_FILE"
    echo "    Restore: scripts/restore-db.sh $BACKUP_FILE"
fi
