#!/usr/bin/env sh
set -eu
SCRIPT_DIR=$(dirname "$0")
cd "$SCRIPT_DIR"
SCRIPT_DIR=$(pwd -P)
mkdir -p backups
STAMP=$(date +%Y%m%d-%H%M%S)
docker compose exec -T web python -c "import sqlite3; source=sqlite3.connect('/app/data/autodev.db'); target=sqlite3.connect('/app/data/autodev-backup.db'); source.backup(target); target.close(); source.close()"
docker compose exec -T web python -c "import tarfile; archive=tarfile.open('/tmp/autodev-$STAMP.tar.gz','w:gz'); archive.add('/app/data/autodev-backup.db',arcname='autodev-backup.db'); archive.add('/app/data/deliveries',arcname='deliveries'); archive.close()"
docker compose cp "web:/tmp/autodev-$STAMP.tar.gz" "backups/autodev-$STAMP.tar.gz"
docker compose exec -T web rm -f "/tmp/autodev-$STAMP.tar.gz" /app/data/autodev-backup.db
echo "备份完成：$SCRIPT_DIR/backups/autodev-$STAMP.tar.gz"
