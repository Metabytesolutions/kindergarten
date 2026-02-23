#!/bin/bash
# ═══════════════════════════════════════════════════════
#  Prosper RFID Platform — Restore Script
#  Run on TARGET NUC after fresh install
# ═══════════════════════════════════════════════════════
set -e
RED='\033[0;31m' GREEN='\033[0;32m' YELLOW='\033[1;33m' NC='\033[0m'
log()  { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
err()  { echo -e "${RED}❌ $1${NC}"; exit 1; }

BACKUP_FILE="$1"
[ -z "$BACKUP_FILE" ] && err "Usage: bash restore.sh <backup_file.tar.gz>"
[ ! -f "$BACKUP_FILE" ] && err "Backup file not found: $BACKUP_FILE"

WORK_DIR="/tmp/prosper_restore_$$"
mkdir -p "$WORK_DIR"
trap "rm -rf $WORK_DIR" EXIT

echo ""
echo "═══════════════════════════════════════════"
echo "  Prosper RFID Platform — Restore"
echo "  Source: $BACKUP_FILE"
echo "  $(date)"
echo "═══════════════════════════════════════════"
echo ""

# STEP 1: Extract
log "Extracting backup..."
tar -xzf "$BACKUP_FILE" -C "$WORK_DIR" --strip-components=1
cat "$WORK_DIR/manifest.json" 2>/dev/null | python3 -m json.tool 2>/dev/null || true

# STEP 2: Verify containers running
log "Checking Docker stack..."
docker compose up -d
echo "⏳ Waiting for PostgreSQL..."
sleep 15
docker exec prosper-postgres pg_isready -U prosper_user \
  || err "PostgreSQL not ready"

# STEP 3: Restore database
log "Restoring database..."
if [ -f "$WORK_DIR/prosper_db.dump" ]; then
  # Drop and recreate for clean restore
  docker exec prosper-postgres psql \
    -U prosper_user -d postgres \
    -c "DROP DATABASE IF EXISTS prosper_db;" 2>/dev/null || true
  docker exec prosper-postgres psql \
    -U prosper_user -d postgres \
    -c "CREATE DATABASE prosper_db;" 2>/dev/null || true

  # Restore
  cat "$WORK_DIR/prosper_db.dump" | docker exec -i prosper-postgres \
    pg_restore -U prosper_user -d prosper_db \
    --no-owner --no-acl --if-exists -c 2>/dev/null || true
  log "Database restored from full dump"

else
  warn "No full dump found — restoring from CSV files..."

  # Restore critical tables from CSV
  for TABLE in zones users students ble_gateways ble_tags \
               school_settings teacher_zones; do
    CSV="$WORK_DIR/${TABLE}.csv"
    if [ -f "$CSV" ]; then
      docker exec prosper-postgres psql -U prosper_user -d prosper_db \
        -c "\COPY $TABLE FROM STDIN CSV HEADER" < "$CSV" 2>/dev/null \
        && log "  Restored $TABLE" \
        || warn "  Skipped $TABLE"
    fi
  done
fi

# STEP 4: Restore .env
if [ -f "$WORK_DIR/env.backup" ]; then
  cp "$WORK_DIR/env.backup" \
     "$HOME/prosper-platform/.env"
  log "Environment config restored"
fi

# STEP 5: Restart stack with restored data
log "Restarting platform..."
cd "$HOME/prosper-platform"
docker compose restart app-server
sleep 10

# STEP 6: Verify
log "Verifying restore..."
docker exec prosper-postgres psql -U prosper_user -d prosper_db -c "
SELECT
  (SELECT COUNT(*) FROM users)        as users,
  (SELECT COUNT(*) FROM students)     as students,
  (SELECT COUNT(*) FROM ble_tags)     as tags,
  (SELECT COUNT(*) FROM ble_gateways) as gateways,
  (SELECT COUNT(*) FROM zones)        as zones;
" 2>/dev/null

echo ""
echo "═══════════════════════════════════════════"
echo "  ✅ RESTORE COMPLETE"
echo "═══════════════════════════════════════════"
echo ""
echo "  ⚠️  Don't forget:"
echo "  1. Run Windows port forwarding (netsh commands)"
echo "  2. Update gateway MQTT broker IP if changed"
echo "  3. Login: admin / Admin1234!"
echo ""
