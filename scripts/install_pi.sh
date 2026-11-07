#!/usr/bin/env bash
# STRIX-1 installer for Raspberry Pi 5 / Raspberry Pi OS 64-bit (Bookworm or newer).
#
# Safe by default: without flags it only installs packages, creates the Python
# virtualenv, creates .env (if missing) and builds the dashboard. Anything that
# changes system configuration requires an explicit flag and is printed first:
#
#   --configure-boot   enable I2C (raspi-config) and append the STRIX-1 overlay
#                      block to /boot/firmware/config.txt (backup made first)
#   --groups           add the current user to i2c, gpio and video groups
#   --systemd          install + enable the strix1.service unit (autostart)
#   --yes              do not ask for confirmation
#   --skip-apt         skip apt package installation
#   --skip-frontend    skip the dashboard build
#
# Nothing is ever deleted. Re-running is safe (idempotent).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="${SUDO_USER:-$(id -un)}"
YES=0 BOOT=0 GROUPS_=0 SYSTEMD=0 SKIP_APT=0 SKIP_FE=0
for a in "$@"; do
  case "$a" in
    --yes|-y) YES=1 ;; --configure-boot) BOOT=1 ;; --groups) GROUPS_=1 ;; --systemd) SYSTEMD=1 ;;
    --skip-apt) SKIP_APT=1 ;; --skip-frontend) SKIP_FE=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[warn]\033[0m %s\n' "$*"; }
confirm() { [ "$YES" = 1 ] && return 0; read -r -p "$1 [y/N] " r; [[ "$r" =~ ^[Yy]$ ]]; }
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"

MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || echo unknown)"
say "Device: $MODEL"
[[ "$MODEL" == *"Raspberry Pi 5"* ]] || warn "This is not a Raspberry Pi 5. Hardware backends will report 'disconnected'; use STRIX_MOCK=true for a demo."
[ "$(uname -m)" = "aarch64" ] || warn "Not a 64-bit OS (uname -m = $(uname -m)). Raspberry Pi OS 64-bit is required for Picamera2/llama.cpp performance."

# ------------------------------------------------------------------ apt
APT_PKGS=(python3-venv python3-dev python3-pip python3-picamera2 python3-av python3-opencv python3-numpy
          python3-lgpio python3-gpiozero python3-libgpiod i2c-tools rpicam-apps git curl build-essential)
if [ "$SKIP_APT" = 0 ]; then
  say "Will install apt packages: ${APT_PKGS[*]}"
  if confirm "Install these packages with apt?"; then
    $SUDO apt-get update
    # rpicam-apps may be named libcamera-apps on older images; install what exists.
    avail=()
    for p in "${APT_PKGS[@]}"; do
      if apt-cache show "$p" >/dev/null 2>&1; then avail+=("$p"); else warn "package $p not available — skipping"; fi
    done
    $SUDO apt-get install -y "${avail[@]}"
  fi
fi

# ----------------------------------------------------------- python venv
say "Creating Python virtualenv (with system site-packages for picamera2/numpy/opencv/gpiozero)"
cd "$REPO/backend"
if [ ! -d .venv ]; then python3 -m venv --system-site-packages .venv; fi
.venv/bin/python -m pip install --upgrade pip wheel >/dev/null
.venv/bin/python -m pip install -r requirements-pi.txt
.venv/bin/python - <<'PY'
import importlib
for m in ("fastapi", "uvicorn", "numpy", "cv2", "picamera2", "gpiozero", "board", "adafruit_mpu6050", "adafruit_dht"):
    try:
        importlib.import_module(m); print(f"  ok   {m}")
    except Exception as e:  # noqa: BLE001
        print(f"  MISS {m}: {e.__class__.__name__}: {e}")
PY

# ------------------------------------------------------------------ .env
cd "$REPO"
if [ ! -f .env ]; then
  say "Creating .env from .env.example with a fresh random access token"
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  sed "s|^STRIX_AUTH_TOKEN=.*|STRIX_AUTH_TOKEN=${TOKEN}|" .env.example > .env
  chmod 600 .env
  printf '\n  Dashboard access token: \033[1m%s\033[0m\n  (stored in %s/.env — keep it private)\n\n' "$TOKEN" "$REPO"
else
  say ".env already exists — leaving it unchanged"
fi
mkdir -p "$REPO/data"

# -------------------------------------------------------------- frontend
if [ "$SKIP_FE" = 0 ]; then
  NODE_OK=0
  if command -v node >/dev/null; then
    NODE_OK=$(node -e 'const [a,b]=process.versions.node.split(".").map(Number); console.log((a>20||(a===20&&b>=19))?1:0)')
  fi
  if [ "$NODE_OK" = 1 ]; then
    say "Building dashboard with Node $(node --version)"
    (cd "$REPO/frontend" && npm ci --no-audit --no-fund && npm run build)
  else
    warn "Node.js >= 20.19 not found (Raspberry Pi OS ships an older Node)."
    warn "Either install Node 22 (see docs/SETUP_PI.md) or build on your laptop:"
    warn "  cd frontend && npm ci && npm run build && rsync -a dist/ ${USER_NAME}@$(hostname).local:${REPO}/frontend/dist/"
  fi
fi

# ------------------------------------------------- optional system changes
CFG=/boot/firmware/config.txt; [ -f "$CFG" ] || CFG=/boot/config.txt
if [ "$BOOT" = 1 ]; then
  say "Boot configuration ($CFG)"
  if command -v raspi-config >/dev/null; then
    confirm "Enable I2C via raspi-config?" && $SUDO raspi-config nonint do_i2c 0
  fi
  if grep -q "STRIX-1 BEGIN" "$CFG" 2>/dev/null; then
    say "STRIX-1 overlay block already present in $CFG"
  else
    echo "Block to append:"; cat "$REPO/config/boot-config.txt"
    if confirm "Append this block to $CFG (a timestamped backup is made first)?"; then
      $SUDO cp "$CFG" "$CFG.strix1-backup-$(date +%Y%m%d%H%M%S)"
      cat "$REPO/config/boot-config.txt" | $SUDO tee -a "$CFG" >/dev/null
      warn "Reboot required for overlay changes to take effect."
    fi
  fi
fi

if [ "$GROUPS_" = 1 ]; then
  for g in i2c gpio video; do
    if getent group "$g" >/dev/null; then $SUDO usermod -aG "$g" "$USER_NAME"; say "added $USER_NAME to $g"; fi
  done
  warn "Log out/in (or reboot) for group membership to apply."
fi

if [ "$SYSTEMD" = 1 ]; then
  UNIT=/etc/systemd/system/strix1.service
  say "Installing $UNIT (User=$USER_NAME, repo=$REPO)"
  sed -e "s|__USER__|$USER_NAME|g" -e "s|__REPO__|$REPO|g" "$REPO/config/systemd/strix1.service" | $SUDO tee "$UNIT" >/dev/null
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now strix1.service
  $SUDO systemctl --no-pager status strix1.service | head -n 5 || true
fi

PORT="$(grep -E '^STRIX_PORT=' .env | cut -d= -f2 | awk '{print $1}')"; PORT="${PORT:-8000}"
say "Done. Start manually with:  $REPO/scripts/run_backend.sh"
say "Then open  http://$(hostname).local:${PORT}  or  http://$(hostname -I 2>/dev/null | awk '{print $1}'):${PORT}  from your laptop."
say "Verify hardware:  scripts/verify_all.sh"
