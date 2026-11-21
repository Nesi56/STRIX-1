#!/usr/bin/env bash
# One-time setup for STRIX-1's simulation: PX4 SITL + Gazebo Harmonic, on Ubuntu 22.04 / 24.04
# (also Ubuntu in WSL2 on Windows 11). Takes 20-40 minutes and ~10 GB of disk; asks for sudo.
#
#   scripts/sim/setup_px4_gz.sh            install into ~/PX4-Autopilot
#   PX4_DIR=/path scripts/sim/setup_px4_gz.sh
#
# Then: scripts/sim/run_fleet.sh   (see docs/SIMULATION.md)
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PX4_DIR="${PX4_DIR:-$HOME/PX4-Autopilot}"
PX4_TAG="${PX4_TAG:-v1.16.2}"
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

. /etc/os-release
[ "${ID:-}" = ubuntu ] || die "this script supports Ubuntu 22.04/24.04 (on Windows: Ubuntu in WSL2)"
grep -qi microsoft /proc/version 2>/dev/null && say "WSL detected: Gazebo's window needs Windows 11 (WSLg); otherwise use run_fleet.sh --headless"

# 1. PX4 source (full clone: PX4's build reads its own git tags)
if [ ! -d "$PX4_DIR/.git" ]; then
  say "Downloading PX4 $PX4_TAG into $PX4_DIR (a few GB)"
  git clone --branch "$PX4_TAG" --recursive https://github.com/PX4/PX4-Autopilot.git "$PX4_DIR"
else
  say "Using the PX4 already in $PX4_DIR"
fi

# 2. PX4's own installer: build tools + Gazebo Harmonic (packages.osrfoundation.org). No NuttX (flight-board) toolchain.
say "Installing PX4's build tools and Gazebo Harmonic (sudo)"
bash "$PX4_DIR/Tools/setup/ubuntu.sh" --no-nuttx

# 3. Gazebo's Python bindings: STRIX-1 reads each drone's camera through them
say "Installing Gazebo's Python bindings"
sudo apt-get install -y python3-gz-transport13 python3-gz-msgs10

# 4. Build PX4 SITL (Gazebo bridge included)
say "Building PX4 SITL (10-20 minutes the first time)"
(cd "$PX4_DIR" && make px4_sitl_default)

# 5. STRIX-1: backend with MAVLink support, YOLO model, dashboard and hub
say "Preparing STRIX-1"
cd "$REPO/backend"
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install -q -r requirements-dev.txt
cd "$REPO"
ls models/*.onnx >/dev/null 2>&1 || scripts/get_detector_model.sh || warn "YOLO model download failed; run scripts/get_detector_model.sh later"
if command -v npm >/dev/null; then (cd frontend && npm ci --no-audit --no-fund && npm run build); else warn "Node.js missing: install it to build the dashboard"; fi
if command -v cargo >/dev/null; then (cd hub && cargo build --release); else warn "Rust missing: install it from https://rustup.rs to build the hub"; fi

# 6. Download the people/car models once (best effort; the world also works without them)
say "Downloading Gazebo Fuel models for the search area (optional)"
grep -v '^\s*#' sim/worlds/strix_field.objects | cut -d'|' -f2 | xargs -I{} echo {} | sort -u | while read -r uri; do
  [ -n "$uri" ] && (gz fuel download -u "$uri" >/dev/null 2>&1 && echo "  cached $uri" || warn "could not download $uri")
done

mkdir -p data/sim && echo "$PX4_DIR" > data/sim/px4_dir
cat <<EOF

  Done. Start a simulated fleet of 3 drones:
      scripts/sim/run_fleet.sh
  Tip: install QGroundControl (https://qgroundcontrol.com) to fly them.
EOF
