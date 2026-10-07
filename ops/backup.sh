#!/usr/bin/env bash
# Daily backup for the trading bot.
# Backs up: SQLite database, config YAML files, .env (without committing it).
# Usage: bash ops/backup.sh [backup-dir]
#
# Schedule with cron (example — runs at 18:00 every day):
#   0 18 * * * cd /path/to/trading-bot && bash ops/backup.sh >> logs/backup.log 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_ROOT="${1:-$PROJECT_DIR/backups}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$BACKUP_ROOT/$TIMESTAMP"

mkdir -p "$DEST"

# Database — use SQLite online backup so we don't copy a mid-write file
DB_PATH="$PROJECT_DIR/trading_bot.db"
if [ -f "$DB_PATH" ]; then
    sqlite3 "$DB_PATH" ".backup '$DEST/trading_bot.db'"
    echo "[$TIMESTAMP] database backed up ($(du -sh "$DEST/trading_bot.db" | cut -f1))"
else
    echo "[$TIMESTAMP] WARNING: database not found at $DB_PATH"
fi

# Config YAMLs (no secrets — safe to keep)
cp "$PROJECT_DIR/config/settings.yaml"     "$DEST/"
cp "$PROJECT_DIR/config/risk_limits.yaml"  "$DEST/"
cp "$PROJECT_DIR/config/watchlist.yaml"    "$DEST/"
echo "[$TIMESTAMP] config YAMLs backed up"

# .env (contains secrets — backup is in backups/ which is gitignored)
if [ -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env" "$DEST/env.bak"
    chmod 600 "$DEST/env.bak"
    echo "[$TIMESTAMP] .env backed up (mode 600)"
fi

# Keep only the 14 most recent backups (two weeks at daily cadence)
KEEP=14
COUNT=$(ls -1d "$BACKUP_ROOT"/*/  2>/dev/null | wc -l | tr -d ' ')
if [ "$COUNT" -gt "$KEEP" ]; then
    TO_DELETE=$((COUNT - KEEP))
    ls -1d "$BACKUP_ROOT"/*/ | head -"$TO_DELETE" | xargs rm -rf
    echo "[$TIMESTAMP] pruned $TO_DELETE old backup(s), keeping $KEEP"
fi

echo "[$TIMESTAMP] backup complete → $DEST"
