#!/bin/bash
# job-scout installer for Linux. Run from anywhere:
#     curl -fsSL https://raw.githubusercontent.com/JvWageningen/job-scout/main/deploy/install.sh | bash
# Idempotent: run it again to update.
set -eu

log() { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mWARNING:\033[0m %s\n' "$*"; }
die() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# Git Bash / MSYS runs Windows Docker with Windows path rules; this script
# would configure it wrongly. The PowerShell installer handles that world.
case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*) die "On Windows, use the PowerShell installer instead:
  powershell -ExecutionPolicy Bypass -Command \"iwr -useb https://raw.githubusercontent.com/JvWageningen/job-scout/main/deploy/install.ps1 | iex\"" ;;
esac

TEMP_DIR=""
cleanup() { [ -z "$TEMP_DIR" ] || rm -rf "$TEMP_DIR"; }
trap cleanup EXIT

# Ask a y/N question. Works under "curl | bash", where stdin is the script
# itself -- the answer must come from the terminal, not from stdin. With no
# terminal at all, the answer is the safe default: no.
confirm() {
    printf '%s (y/N): ' "$1"
    local reply=""
    if [ -r /dev/tty ]; then
        read -r reply </dev/tty || reply=""
    else
        printf '(no terminal -- assuming no)\n'
    fi
    [ "$reply" = "y" ] || [ "$reply" = "Y" ]
}

# --- install directory -------------------------------------------------------
# Running inside a checkout (developer or NAS): use it as-is and skip the
# download, so local changes are never overwritten with GitHub's copy.
if [ -f pyproject.toml ] && [ -f docker-compose.yml ]; then
    INSTALL_DIR="$(pwd)"
    FROM_CHECKOUT=1
    log "Using this checkout: $INSTALL_DIR"
else
    INSTALL_DIR="$HOME/job-scout"
    FROM_CHECKOUT=0
fi
ENV_FILE="$INSTALL_DIR/.env"
DATA_DIR="$INSTALL_DIR/data"

# --- docker ------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    printf '\n\033[1mDocker is not installed.\033[0m\n'
    printf 'This script can install it using the official Docker script\n'
    printf '(https://get.docker.com), which needs sudo.\n\n'
    if confirm "Install Docker now?"; then
        curl -fsSL https://get.docker.com | sudo sh || die "Docker installation failed."
        log "Docker installed."
        warn "You may need to log out and back in before docker works without sudo."
    else
        die "Docker is required. Install it (https://docs.docker.com/engine/install/) and re-run this script."
    fi
fi

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
    if sudo -n docker info >/dev/null 2>&1; then
        DOCKER="sudo docker"
        log "docker needs sudo on this machine; using sudo"
    else
        die "Cannot reach the Docker service.

Start it:                sudo systemctl start docker
Run without sudo later:  sudo usermod -aG docker \$USER   (then log out and back in)
Then re-run this script."
    fi
fi

if $DOCKER compose version >/dev/null 2>&1; then
    COMPOSE="$DOCKER compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    die "Docker is installed but Docker Compose is missing. Install the compose plugin and re-run."
fi

# --- download and unpack -----------------------------------------------------
if [ "$FROM_CHECKOUT" = "0" ]; then
    TEMP_DIR=$(mktemp -d)
    ARCHIVE_URL="https://github.com/JvWageningen/job-scout/archive/refs/heads/main.tar.gz"
    log "Downloading the latest job-scout..."
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "$TEMP_DIR/src.tar.gz" "$ARCHIVE_URL" \
            || die "Download failed. Check your internet connection and try again."
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$TEMP_DIR/src.tar.gz" "$ARCHIVE_URL" \
            || die "Download failed. Check your internet connection and try again."
    else
        die "Neither curl nor wget is installed; install one and re-run."
    fi
    tar -xzf "$TEMP_DIR/src.tar.gz" -C "$TEMP_DIR" || die "Could not unpack the download."
    [ -d "$TEMP_DIR/job-scout-main" ] || die "Unexpected archive layout (no job-scout-main/)."

    mkdir -p "$INSTALL_DIR"
    log "Installing into $INSTALL_DIR"
    # /. copies dotfiles too (a bare * would miss .env.example). The archive
    # holds only tracked files, so an existing .env and data/ are untouched.
    cp -r "$TEMP_DIR/job-scout-main/." "$INSTALL_DIR/" || die "Could not copy files into $INSTALL_DIR."
fi

# --- .env ----------------------------------------------------------------
if [ ! -f "$ENV_FILE" ]; then
    [ -f "$INSTALL_DIR/.env.example" ] || die "No .env.example found; the download looks incomplete."
    log "Creating .env with default settings"
    cp "$INSTALL_DIR/.env.example" "$ENV_FILE"
fi

PORT=$(grep "^JOB_SCOUT_PORT=" "$ENV_FILE" | tail -1 | cut -d= -f2)
[ -n "$PORT" ] || PORT="24817"

# Docker Desktop (including WSL2 integration) cannot use host networking, so
# it needs the desktop override; native Linux Docker keeps host networking,
# which also keeps wake-on-LAN working. Asking the daemon is the only
# detection that survives WSL2, where uname claims plain Linux.
if $DOCKER info --format '{{.OperatingSystem}}' 2>/dev/null | grep -qi "docker desktop"; then
    if ! grep -q "docker-compose.desktop.yml" "$ENV_FILE"; then
        log "Docker Desktop detected -- enabling the desktop networking override"
        if grep -q "^COMPOSE_FILE=" "$ENV_FILE"; then
            sed -i.bak "s|^COMPOSE_FILE=.*|COMPOSE_FILE=docker-compose.yml:docker-compose.desktop.yml|" "$ENV_FILE"
            rm -f "$ENV_FILE.bak"
        else
            echo "COMPOSE_FILE=docker-compose.yml:docker-compose.desktop.yml" >> "$ENV_FILE"
        fi
    fi
fi

# --- pre-flight ----------------------------------------------------------
# Skip the port check when our own container already holds it (update run).
if ! $DOCKER ps --format '{{.Names}}' 2>/dev/null | grep -qx "job-scout-web"; then
    if command -v ss >/dev/null 2>&1 && ss -tln 2>/dev/null | grep -q ":$PORT "; then
        die "Something on this machine is already using port $PORT.
Edit $ENV_FILE, change JOB_SCOUT_PORT to a free port, and re-run this script."
    fi
fi

mkdir -p "$DATA_DIR"
# The containers write as uid 1000. When that is not you, open up the dir.
if [ "$(id -u)" != "1000" ]; then
    chmod -R a+rwX "$DATA_DIR" 2>/dev/null || warn "Could not adjust permissions on $DATA_DIR.
If the dashboard cannot save anything, run:  sudo chmod -R a+rwX $DATA_DIR"
fi

# --- build and start -------------------------------------------------------
cd "$INSTALL_DIR"
log "Building the container image (the first build takes a few minutes)..."
$COMPOSE build || die "The build failed. The messages above say why; fix that and re-run."

log "Starting job-scout..."
$COMPOSE up -d || die "Containers failed to start. Run '$COMPOSE logs' in $INSTALL_DIR to see why."

log "Waiting for the dashboard..."
i=0
while [ "$i" -lt 60 ]; do
    if curl -sf -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; then
        break
    fi
    i=$((i + 1))
    sleep 1
done
if [ "$i" -ge 60 ]; then
    warn "The dashboard did not answer within 60s. Recent logs:"
    $COMPOSE logs --tail 40
    exit 1
fi

LAN_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="src") print $(i+1); exit}' || true)
[ -n "${LAN_IP:-}" ] || LAN_IP="localhost"

printf '\n'
log "job-scout is running."
printf '\n'
printf '  \033[1mDashboard:\033[0m  http://%s:%s   (open this in your browser)\n' "$LAN_IP" "$PORT"
printf '  \033[1mYour data:\033[0m  %s\n' "$DATA_DIR"
printf '\n'
printf '  Stop:     cd "%s" && %s down\n' "$INSTALL_DIR" "$COMPOSE"
printf '  Logs:     cd "%s" && %s logs -f\n' "$INSTALL_DIR" "$COMPOSE"
printf '  Update:   re-run this installer\n'
printf '\n'
if grep -q "docker-compose.desktop.yml" "$ENV_FILE"; then
    printf '  \033[33mNote:\033[0m waking a sleeping model server over the network does not work\n'
    printf '  under Docker Desktop. Leave the MAC field empty in the Schedule tab.\n\n'
fi
printf '  Sharing this network with others? Set JOB_SCOUT_DASHBOARD_TOKEN in\n'
printf '  %s and restart to require a password.\n\n' "$ENV_FILE"
