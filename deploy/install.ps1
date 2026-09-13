# job-scout installer for Windows (Docker Desktop).
#
# Run in PowerShell:
#   powershell -ExecutionPolicy Bypass -Command "iwr -useb https://raw.githubusercontent.com/JvWageningen/job-scout/main/deploy/install.ps1 | iex"
#
# Idempotent: run it again to update. Targets Windows PowerShell 5.1, the
# version every Windows 10/11 machine ships with -- no PS7-only syntax here.

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$ZipUrl = 'https://github.com/JvWageningen/job-scout/archive/refs/heads/main.zip'
$InstallDir = Join-Path $HOME 'job-scout'
$TempDir = $null

function Write-Log { param([string]$Message) Write-Host -ForegroundColor Cyan "==> $Message" }
function Write-Warn { param([string]$Message) Write-Host -ForegroundColor Yellow "WARNING: $Message" }
function Fail { param([string]$Message) Write-Host -ForegroundColor Red "ERROR: $Message"; exit 1 }

# Native commands (docker, winget) do NOT throw on failure in PowerShell --
# only $LASTEXITCODE tells the truth. Every native call below checks it.
function Get-DockerStatus {
    $cli = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $cli) { return 'missing' }
    & docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { return 'running' }
    return 'stopped'
}

function Install-DockerDesktop {
    Write-Host ""
    Write-Host "Docker Desktop is required and was not found." -ForegroundColor Yellow
    Write-Host ""

    # Never install software without a human saying yes.
    if (-not [Environment]::UserInteractive) {
        Write-Host "Install it from https://www.docker.com/products/docker-desktop/"
        Write-Host "then re-run this installer."
        exit 1
    }

    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        $answer = Read-Host "Install Docker Desktop now using winget? (y/N)"
        if ($answer -eq 'y' -or $answer -eq 'Y') {
            Write-Log "Installing Docker Desktop (this downloads ~500 MB)..."
            & winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
            if ($LASTEXITCODE -ne 0) {
                Fail "winget could not install Docker Desktop. Install it manually from https://www.docker.com/products/docker-desktop/ and re-run this installer."
            }
            Write-Host ""
            Write-Warn "Docker Desktop is installed but needs a first manual start:"
            Write-Host "  1. Log out and back in (or reboot)"
            Write-Host "  2. Start Docker Desktop and wait for the whale icon to settle"
            Write-Host "  3. Re-run this installer"
            exit 0
        }
    }
    Start-Process "https://www.docker.com/products/docker-desktop/"
    Write-Host "Install Docker Desktop from the page that just opened, start it once,"
    Write-Host "then re-run this installer."
    exit 1
}

try {
    # PowerShell 5.1 negotiates TLS 1.0 by default; GitHub requires 1.2.
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    Write-Host ""
    Write-Log "job-scout installer for Windows"

    # --- Docker ---
    $status = Get-DockerStatus
    if ($status -eq 'missing') { Install-DockerDesktop }
    if ($status -eq 'stopped') {
        Write-Warn "Docker is installed but not running."
        Write-Host "Start Docker Desktop, wait for the whale icon in the taskbar to"
        Write-Host "settle, then re-run this installer."
        exit 1
    }
    Write-Log "Docker is running"

    # --- Download and unpack ---
    if (-not (Test-Path $InstallDir)) {
        $null = New-Item -ItemType Directory -Path $InstallDir -Force
    }
    $TempDir = Join-Path $env:TEMP "job-scout-install-$PID"
    if (Test-Path $TempDir) { Remove-Item -Path $TempDir -Recurse -Force }
    $null = New-Item -ItemType Directory -Path $TempDir -Force

    Write-Log "Downloading the latest job-scout..."
    $ZipPath = Join-Path $TempDir 'src.zip'
    Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipPath -UseBasicParsing

    Write-Log "Unpacking..."
    Expand-Archive -Path $ZipPath -DestinationPath $TempDir -Force
    $SourceDir = Join-Path $TempDir 'job-scout-main'
    if (-not (Test-Path $SourceDir)) { Fail "Unexpected archive layout (no job-scout-main folder)." }

    Write-Log "Installing into $InstallDir"
    # Wildcard copy merges into existing folders. Copying the folder itself
    # would nest a second copy inside on update (src\src\...), a classic
    # Copy-Item trap. The archive holds only tracked files, so an existing
    # .env and data folder are never touched.
    Copy-Item -Path (Join-Path $SourceDir '*') -Destination $InstallDir -Recurse -Force

    # --- .env ---
    $EnvFile = Join-Path $InstallDir '.env'
    if (-not (Test-Path $EnvFile)) {
        $EnvExample = Join-Path $InstallDir '.env.example'
        if (-not (Test-Path $EnvExample)) { Fail ".env.example is missing; the download looks incomplete." }
        Write-Log "Creating .env with default settings"
        Copy-Item -Path $EnvExample -Destination $EnvFile
    }

    $envText = Get-Content -Path $EnvFile -Raw
    if ($envText -notmatch 'COMPOSE_FILE') {
        Write-Log "Configuring Docker Desktop networking"
        # ASCII: Add-Content's default UTF-16 would put a BOM in front of the
        # first key and docker compose would silently ignore the file.
        Add-Content -Path $EnvFile -Encoding ASCII -Value ""
        Add-Content -Path $EnvFile -Encoding ASCII -Value "# Docker Desktop cannot use host networking; use the desktop override."
        Add-Content -Path $EnvFile -Encoding ASCII -Value "COMPOSE_FILE=docker-compose.yml;docker-compose.desktop.yml"
        Add-Content -Path $EnvFile -Encoding ASCII -Value "COMPOSE_PATH_SEPARATOR=;"
    }

    $Port = 24817
    $portMatch = Get-Content -Path $EnvFile | Select-String -Pattern 'JOB_SCOUT_PORT\s*=\s*(\d+)' | Select-Object -First 1
    if ($portMatch) { $Port = [int]$portMatch.Matches[0].Groups[1].Value }

    # --- Build and start ---
    Push-Location $InstallDir
    try {
        Write-Log "Building the container image (the first build takes several minutes)..."
        & docker compose build
        if ($LASTEXITCODE -ne 0) { Fail "The build failed. The messages above say why; fix that and re-run." }

        Write-Log "Starting job-scout..."
        & docker compose up -d
        if ($LASTEXITCODE -ne 0) { Fail "Containers failed to start. Run 'docker compose logs' in $InstallDir to see why." }

        Write-Log "Waiting for the dashboard..."
        $ready = $false
        for ($i = 0; $i -lt 45; $i++) {
            try {
                $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 5
                if ($response.StatusCode -eq 200) { $ready = $true; break }
            } catch { }
            Start-Sleep -Seconds 2
        }
        if (-not $ready) {
            Write-Warn "The dashboard did not answer within 90 seconds."
            Write-Host "It may still be starting. Check with:"
            Write-Host "  cd `"$InstallDir`""
            Write-Host "  docker compose logs -f"
            exit 1
        }

        Write-Host ""
        Write-Log "job-scout is running."
        Write-Host ""
        Write-Host "  Dashboard:  http://localhost:$Port  (opening it now)"
        Write-Host "  Your data:  $(Join-Path $InstallDir 'data')"
        Write-Host ""
        Write-Host "  Stop:     cd `"$InstallDir`"; docker compose down"
        Write-Host "  Logs:     cd `"$InstallDir`"; docker compose logs -f"
        Write-Host "  Update:   re-run this installer"
        Write-Host ""
        Write-Warn "Waking a sleeping model server over the network does not work under"
        Write-Host "  Docker Desktop. Leave the MAC field empty in the Schedule tab."
        Write-Host ""
        Write-Host "  Sharing this network with others? Set JOB_SCOUT_DASHBOARD_TOKEN in"
        Write-Host "  $EnvFile and restart to require a password."
        Write-Host ""

        Start-Process "http://localhost:$Port/"
    } finally {
        Pop-Location
    }
} catch {
    Fail $_.Exception.Message
} finally {
    if ($TempDir -and (Test-Path $TempDir)) {
        Remove-Item -Path $TempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
