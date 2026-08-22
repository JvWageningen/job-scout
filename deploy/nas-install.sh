# Install job-scout on an ASUSTOR NAS (ADM).
#
# Run it on the NAS, from anywhere:
#     sh /volume1/Docker/job-scout/deploy/nas-install.sh
#
# It is idempotent: run it again after editing .env or pulling new code.
set -eu

# ADM does not put the Docker Engine app on PATH for SSH sessions.
for d in /usr/local/AppCentral/docker-ce/bin /usr/local/AppCentral/docker/bin; do
    [ -d "$d" ] && PATH="$d:$PATH"
done
export PATH

PROJECT_DIR=$(cd "$(dirname "$0")/.." && pwd)
DATA_DIR="$PROJECT_DIR/data"
ENV_FILE="$PROJECT_DIR/.env"
CONTAINER_UID=1000          # the 'scout' user baked into the image

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mWARNING:\033[0m %s\n' "$*"; }
die() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# --- prerequisites ---------------------------------------------------------
command -v docker >/dev/null 2>&1 || die \
"Docker not found.

Install 'Docker Engine' from App Central in ADM, then run this script again.
If it is installed, its binaries live under /usr/local/AppCentral/docker-ce/bin."

# On ADM the docker socket is root-only, so an ordinary account needs sudo.
# Prefer unprivileged and fall back to sudo only if it does not prompt, since
# this script may be run unattended.
DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
    if sudo -n docker info >/dev/null 2>&1; then
        DOCKER="sudo docker"
        log "docker needs sudo here; using passwordless sudo"
    fi
fi

$DOCKER info >/dev/null 2>&1 || die "Cannot talk to the Docker daemon.

Start the Docker Engine app in ADM, then give this account access, either by
adding it to the docker group or with a passwordless sudo rule:

  echo \"$(id -un) ALL=(root) NOPASSWD: $(command -v docker)\" | sudo tee /etc/sudoers.d/job-scout-docker
  sudo chmod 440 /etc/sudoers.d/job-scout-docker"

if $DOCKER compose version >/dev/null 2>&1; then
    COMPOSE="$DOCKER compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="${DOCKER%docker}docker-compose"
else
    die "Neither 'docker compose' nor 'docker-compose' is available."
fi
log "Using: $COMPOSE"

# --- configuration ---------------------------------------------------------
if [ ! -f "$ENV_FILE" ]; then
    [ -f "$PROJECT_DIR/.env.example" ] \
        || die "No .env and no .env.example to seed it from."
    log "Creating .env from .env.example"
    cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
    warn "Edit $ENV_FILE before the first scheduled run:
  - JOB_SCOUT_WAKE_MAC        MAC of the machine running the model server
  - JOB_SCOUT_LLM_HEALTH_URL  its /v1/models endpoint
Leaving the MAC empty disables waking, which is correct only if that machine
is always on."
fi

# shellcheck disable=SC1090
. "$ENV_FILE"
PORT="${JOB_SCOUT_PORT:-24817}"

# The container runs as uid 1000 and writes SQLite databases into /data, so
# the bind-mounted directory has to exist and be writable by that uid up front.
mkdir -p "$DATA_DIR"
log "Granting uid $CONTAINER_UID access to data/"
chown -R "$CONTAINER_UID:$CONTAINER_UID" "$DATA_DIR" 2>/dev/null \
    || sudo -n chown -R "$CONTAINER_UID:$CONTAINER_UID" "$DATA_DIR" 2>/dev/null \
    || chmod -R a+rwX "$DATA_DIR"

# --- pre-flight checks -----------------------------------------------------
if [ -n "${JOB_SCOUT_LLM_HEALTH_URL:-}" ]; then
    if curl -sf --max-time 8 -o /dev/null "$JOB_SCOUT_LLM_HEALTH_URL" 2>/dev/null; then
        log "Model server is reachable at $JOB_SCOUT_LLM_HEALTH_URL"
    else
        warn "Model server did not answer at $JOB_SCOUT_LLM_HEALTH_URL.
That is expected if it is currently asleep -- the scheduler will wake it. If
it is awake, check the URL and that it listens on an address the NAS can reach."
    fi
else
    warn "JOB_SCOUT_LLM_HEALTH_URL is empty; the scheduler will not wait for
the model server before starting a run."
fi

# --- build and start -------------------------------------------------------
cd "$PROJECT_DIR"
log "Building the image (the first build takes a few minutes on NAS hardware)"
$COMPOSE build

log "Starting the containers"
$COMPOSE up -d

sleep 5
for svc in job-scout-scheduler job-scout-web; do
    if ! $DOCKER ps --format '{{.Names}}' | grep -qx "$svc"; then
        printf '\033[31m%s is not running. Recent logs:\033[0m\n' "$svc"
        $COMPOSE logs --tail 40
        exit 1
    fi
done

IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')
[ -n "${IP:-}" ] || IP=$(hostname -i 2>/dev/null | awk '{print $1}')

log "Waiting for the dashboard"
i=0
while [ "$i" -lt 30 ]; do
    if curl -sf -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; then
        log "Up."
        printf '\n  Dashboard:  \033[1mhttp://%s:%s\033[0m\n\n' "${IP:-<nas-ip>}" "$PORT"
        $DOCKER logs job-scout-scheduler 2>&1 | grep -i "next run" | tail -1
        printf '\n  Logs:    %s logs -f\n' "$COMPOSE"
        printf '  Run now: %s exec scheduler job-scout run --all\n' "$COMPOSE"
        printf '  Stop:    %s down\n' "$COMPOSE"
        printf '  Update:  re-sync this directory, then re-run this script\n\n'
        exit 0
    fi
    i=$((i + 1))
    sleep 2
done

printf '\033[31mContainers are running but the dashboard did not answer.\033[0m\n'
$COMPOSE logs --tail 40
exit 1
