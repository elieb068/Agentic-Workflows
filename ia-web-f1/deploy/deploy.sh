#!/usr/bin/env bash
# Raio-X IA on the Oracle VPS. See DEPLOY.md.
#
#   deploy.sh deploy               git pull, build, migrate, restart, wait until healthy
#   deploy.sh rollback <commit>    same, at an earlier commit
#   deploy.sh run <command...>     one-off CLI: purge | stats [--days N] | email-test <to> | indexnow [--all]
#   deploy.sh backup               pg_dump to $BASE/backups (keeps the newest 14)
#   deploy.sh ps | logs [service]
set -euo pipefail

BASE=${RAIOXIA_BASE:-/home/ubuntu/raioxia}
REPO="$BASE/app"
APP="$REPO/ia-web-f1"
BACKUPS="$BASE/backups"
KEEP_BACKUPS=14
export RAIOXIA_ENV_FILE="$BASE/.env"
export RAIOXIA_SECRETS_DIR="$BASE/secrets"

dc() { docker compose --env-file "$RAIOXIA_ENV_FILE" -f "$APP/deploy/compose.yaml" "$@"; }

wait_healthy() {
  local id status
  for _ in $(seq 1 40); do
    id=$(dc ps -q api)
    status=$(docker inspect -f '{{.State.Health.Status}}' "$id" 2>/dev/null || echo starting)
    if [ "$status" = healthy ]; then echo "api healthy"; return 0; fi
    sleep 3
  done
  echo "api did not become healthy; recent logs:" >&2
  dc logs --tail=60 api migrate >&2
  return 1
}

release() {
  dc build
  dc up -d --remove-orphans
  wait_healthy
  echo "deployed $(git -C "$REPO" rev-parse --short HEAD)"
  dc run --rm cli indexnow || echo "indexnow ping failed (the deploy itself is fine)" >&2
}

case "${1:-}" in
  deploy)
    git -C "$REPO" pull --ff-only
    release
    ;;
  rollback)
    [ -n "${2:-}" ] || { echo "usage: deploy.sh rollback <commit>" >&2; exit 2; }
    git -C "$REPO" checkout --detach "$2"
    release
    echo "back on the branch later with: git -C $REPO switch - && deploy.sh deploy"
    ;;
  run)
    shift
    dc run --rm cli "$@"
    ;;
  backup)
    umask 077
    mkdir -p "$BACKUPS"
    file="$BACKUPS/raioxia_$(date -u +%Y%m%d_%H%M%S).dump"
    dc exec -T postgres pg_dump -U raioxia -d raioxia -Fc > "$file.part"
    mv "$file.part" "$file"
    ls -1t "$BACKUPS"/raioxia_*.dump | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm --
    echo "backup: $file ($(du -h "$file" | cut -f1))"
    ;;
  ps)
    dc ps
    ;;
  logs)
    shift
    dc logs -f --tail=100 "$@"
    ;;
  *)
    sed -n '2,9p' "$0"
    exit 2
    ;;
esac
