# Update job-scout on the NAS to the latest published version.
#
# Fetches origin/main, and rebuilds only when something actually changed, so
# running it often is cheap. Intended for cron:
#
#     30 4 * * 1 sh /volume1/Docker/job-scout/deploy/nas-update.sh
#
# Safe to run while a pipeline run is in progress: compose recreates the
# containers, and the scheduler picks the next slot back up on start.
set -eu

# ADM keeps git (Entware) and the Docker Engine app off the default PATH.
PATH=/opt/bin:/opt/sbin:/usr/local/AppCentral/docker-ce/bin:/usr/local/AppCentral/docker/bin:$PATH
export PATH

PROJECT_DIR=$(cd "$(dirname "$0")/.." && pwd)
LOG_DIR="$PROJECT_DIR/data/logs"
LOG="$LOG_DIR/nas-update.log"

mkdir -p "$LOG_DIR"
log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }

command -v git >/dev/null 2>&1 || { log "ERROR: git not found on PATH"; exit 1; }

DOCKER="docker"
docker info >/dev/null 2>&1 || DOCKER="sudo -n docker"
$DOCKER info >/dev/null 2>&1 || { log "ERROR: cannot reach the Docker daemon"; exit 1; }

if $DOCKER compose version >/dev/null 2>&1; then
    COMPOSE="$DOCKER compose"
else
    COMPOSE="${DOCKER%docker}docker-compose"
fi

cd "$PROJECT_DIR"
git rev-parse --git-dir >/dev/null 2>&1 || { log "ERROR: $PROJECT_DIR is not a git checkout"; exit 1; }

BEFORE=$(git rev-parse HEAD)
log "Checking for updates (currently ${BEFORE%"${BEFORE#???????}"})"

git fetch --quiet origin || { log "ERROR: git fetch failed"; exit 1; }

# --ff-only rather than a reset: if the checkout has diverged, stop and say so
# instead of silently discarding whatever is here.
if ! git merge --ff-only origin/main >/dev/null 2>&1; then
    log "ERROR: cannot fast-forward to origin/main; resolve the checkout by hand"
    exit 1
fi

AFTER=$(git rev-parse HEAD)
if [ "$BEFORE" = "$AFTER" ]; then
    log "Already up to date; nothing to rebuild"
    exit 0
fi

log "Updated to $(git log --oneline -1)"
log "Rebuilding"
if ! $COMPOSE build >>"$LOG" 2>&1; then
    log "ERROR: build failed; leaving the running containers alone"
    exit 1
fi

log "Restarting"
$COMPOSE up -d >>"$LOG" 2>&1

sleep 5
for svc in job-scout-scheduler job-scout-web; do
    if $DOCKER ps --format '{{.Names}}' | grep -qx "$svc"; then
        log "$svc is running"
    else
        log "ERROR: $svc did not come back up"
        $COMPOSE logs --tail 30 >>"$LOG" 2>&1
        exit 1
    fi
done

log "Update complete"
