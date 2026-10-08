#!/bin/bash
set -euo pipefail
umask 077
: "${BACKUP_ENCRYPTION_KEY:?Provide a separate 32-byte base64 backup key}"
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' | .venv/bin/python deploy/encrypt_stream.py encrypt > "backups/database-$stamp.dump.aes.partial"
docker compose exec -T web tar -czf - -C /app media | .venv/bin/python deploy/encrypt_stream.py encrypt > "backups/media-$stamp.tar.gz.aes.partial"
mv "backups/database-$stamp.dump.aes.partial" "backups/database-$stamp.dump.aes"
mv "backups/media-$stamp.tar.gz.aes.partial" "backups/media-$stamp.tar.gz.aes"
sha256sum "backups/database-$stamp.dump.aes" "backups/media-$stamp.tar.gz.aes" > "backups/manifest-$stamp.sha256"
printf 'Encrypted backup completed: %s\n' "$stamp"
