#!/bin/bash
# ═══════════════════════════════════════════════════════
#  Prosper RFID Platform — Backup Script
#  Run on SOURCE NUC to export everything
# ═══════════════════════════════════════════════════════
GREEN='\033[0;32m' YELLOW='\033[1;33m' NC='\033[0m'
log()  { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }

BACKUP_DIR="$HOME/prosper-backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/prosper_backup_$TIMESTAMP.tar.gz"
WORK_DIR="$BACKUP_DIR/tmp_$TIMESTAMP"

mkdir -p "$WORK_DIR"

echo ""
echo "═══════════════════════════════════════════"
echo "  Prosper RFID Platform — Backup"
echo "  $(date)"
echo "═══════════════════════════════════════════"
echo ""

# STEP 1: Full PostgreSQL dump
log "Dumping PostgreSQL database..."
docker exec prosper-postgres pg_dump \
  -U prosper_user \
  --no-owner \
  --no-acl \
  --format=custom \
  prosper_db > "$WORK_DIR/prosper_db.dump"
log "Database dump: $(du -sh $WORK_DIR/prosper_db.dump | cut -f1)"

# STEP 2: Critical tables as CSV (human readable + safe fallback)
log "Exporting critical tables as CSV..."
for TABLE in users students ble_tags ble_gateways zones \
             school_settings teacher_zones student_permitted_zones; do
  docker exec prosper-postgres psql -U prosper_user -d prosper_db \
    -c "\COPY $TABLE TO STDOUT CSV HEADER" \
    > "$WORK_DIR/${TABLE}.csv" 2>/dev/null \
    && echo "  📄 $TABLE: $(wc -l < $WORK_DIR/${TABLE}.csv) rows" \
    || warn "  Skipped $TABLE (may not exist)"
done

# STEP 3: Export .env
log "Saving environment config..."
cp "$HOME/prosper-platform/.env" "$WORK_DIR/env.backup" 2>/dev/null \
  || warn ".env not found"

# STEP 4: Save gateway config
log "Saving gateway config..."
docker exec prosper-postgres psql -U prosper_user -d prosper_db \
  -c "\COPY ble_gateways TO STDOUT CSV HEADER" \
  > "$WORK_DIR/gateway_config.csv" 2>/dev/null

# STEP 5: Summary JSON
log "Writing backup manifest..."
cat > "$WORK_DIR/manifest.json" << JSON
{
  "backup_date": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "platform_version": "v1.0.0-MVP",
  "source_host": "$(hostname)",
  "tables": [
    "users", "students", "ble_tags", "ble_gateways",
    "zones", "school_settings", "teacher_zones"
  ],
  "includes_history": true,
  "notes": "Full platform backup including tag assignments and gateway config"
}
JSON

# STEP 6: Package everything
log "Creating backup archive..."
tar -czf "$BACKUP_FILE" -C "$BACKUP_DIR" "tmp_$TIMESTAMP"
rm -rf "$WORK_DIR"

SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
log "Backup complete!"

echo ""
echo "═══════════════════════════════════════════"
echo "  📦 BACKUP FILE: $BACKUP_FILE"
echo "  📊 SIZE: $SIZE"
echo "═══════════════════════════════════════════"
echo ""
echo "  To copy to new NUC:"
echo "  scp $BACKUP_FILE user@NEW_NUC_IP:~/"
echo ""
echo "  Or copy via Windows Explorer:"
echo "  \\\\wsl\$\\Ubuntu\\home\\sandeep\\prosper-backups\\"
echo ""
