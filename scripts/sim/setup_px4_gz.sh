#!/usr/bin/env bash
# One-time setup for STRIX-1's simulation: PX4 SITL + Gazebo Harmonic. About 30-60 minutes and ~10 GB.
#
#   scripts/sim/setup_px4_gz.sh           pick the route for this Linux automatically
#   scripts/sim/setup_px4_gz.sh --conda   force the conda route (any distro)
#
# Two routes:
#   * Ubuntu 22.04 / 24.04 (also WSL2): system packages via apt, using PX4's own installer (asks for sudo).
#   * Any other Linux (Arch, Artix, Manjaro, Fedora, Debian, ...): a self-contained conda-forge environment
#     with Gazebo Harmonic, compilers and Python in ~/.local/share/strix-sim. Nothing is installed system-wide
#     and no sudo is needed.
# PX4 goes into ~/PX4-Autopilot (set PX4_DIR to change it). Then: scripts/sim/run_fleet.sh
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PX4_DIR="${PX4_DIR:-$HOME/PX4-Autopilot}"
PX4_TAG="${PX4_TAG:-v1.16.2}"
SIM_HOME="${STRIX_SIM_HOME:-$HOME/.local/share/strix-sim}"
ENV="${STRIX_SIM_ENV:-$SIM_HOME/gz}"
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

USE_CONDA=""
[ "${1:-}" = "--conda" ] && USE_CONDA=1
. /etc/os-release 2>/dev/null || true
if [ -z "$USE_CONDA" ] && [ "${ID:-}" != ubuntu ]; then
  USE_CONDA=1
  say "${PRETTY_NAME:-This Linux} is not Ubuntu: using the self-contained conda route (no sudo needed)"
fi
grep -qi microsoft /proc/version 2>/dev/null && say "WSL detected: Gazebo's window needs Windows 11 (WSLg); otherwise use run_fleet.sh --headless"
for tool in git curl tar bzip2 python3; do command -v "$tool" >/dev/null || die "please install $tool first"; done

# ---------------------------------------------------------------- 1. PX4 source
# A full clone: PX4's build reads its own git tags.
if [ ! -d "$PX4_DIR/.git" ]; then
  say "Downloading PX4 $PX4_TAG into $PX4_DIR (a few GB)"
  git clone --branch "$PX4_TAG" --recursive https://github.com/PX4/PX4-Autopilot.git "$PX4_DIR"
else
  say "Using the PX4 already in $PX4_DIR"
fi

# ---------------------------------------------------------------- 2a. conda route
conda_env() {
  # PATH and compilers of the conda environment, for building PX4 and running Gazebo.
  export PATH="$ENV/bin:$PATH" CMAKE_PREFIX_PATH="$ENV" PKG_CONFIG_PATH="$ENV/lib/pkgconfig:$ENV/share/pkgconfig"
  export CC="$ENV/bin/x86_64-conda-linux-gnu-gcc" CXX="$ENV/bin/x86_64-conda-linux-gnu-g++"
  if [ "$(uname -m)" = aarch64 ]; then
    export CC="$ENV/bin/aarch64-conda-linux-gnu-gcc" CXX="$ENV/bin/aarch64-conda-linux-gnu-g++"
  fi
}

patch_px4_for_conda() {
  # conda-forge's Gazebo uses a newer protobuf and OpenCV than Ubuntu's, which PX4 v1.16 doesn't expect.
  # These three edits only touch your PX4 copy; they are safe to run again.
  local d="$PX4_DIR"
  sed -i 's/^set(CMAKE_CXX_STANDARD 14)/set(CMAKE_CXX_STANDARD 17)/' "$d/CMakeLists.txt"  # protobuf needs C++17
  grep -q -- "-Wno-deprecated-declarations" "$d/cmake/px4_add_common_flags.cmake" \
    || sed -i 's/^\(\s*\)-Werror$/\1-Werror\n\1-Wno-deprecated-declarations/' "$d/cmake/px4_add_common_flags.cmake"
  local plugins="$d/src/modules/simulation/gz_plugins/CMakeLists.txt"
  sed -i 's/^\(\s*\)add_subdirectory(optical_flow)/\1# add_subdirectory(optical_flow)  # skipped by STRIX-1 setup: needs OpenCV 4/' "$plugins"
  sed -i 's/ OpticalFlowSystem / /' "$plugins"
}

setup_conda() {
  local mm
  mm="$(command -v micromamba || true)"
  if [ -z "$mm" ]; then
    mm="$SIM_HOME/bin/micromamba"
    if [ ! -x "$mm" ]; then
      say "Downloading micromamba (a small conda package manager) into $SIM_HOME"
      mkdir -p "$SIM_HOME"
      local plat=linux-64; [ "$(uname -m)" = aarch64 ] && plat=linux-aarch64
      curl -fsSL "https://micro.mamba.pm/api/micromamba/$plat/latest" | tar -xj -C "$SIM_HOME" bin/micromamba
    fi
  fi
  if [ ! -x "$ENV/bin/gz" ]; then
    say "Creating the Gazebo Harmonic environment in $ENV (downloads ~2-3 GB)"
    "$mm" create -y -r "$SIM_HOME/mamba" -p "$ENV" -c conda-forge \
      python=3.12 gz-harmonic gz-transport13 gz-msgs10 cxx-compiler c-compiler cmake ninja make pkg-config
  else
    say "Using the Gazebo environment already in $ENV"
  fi
  say "Installing PX4's Python build tools into that environment"
  "$ENV/bin/python" -m pip install -q "empy>=3.3,<4" jinja2 pyyaml kconfiglib jsonschema packaging future \
    pyros-genmsg numpy toml six lxml pyserial psutil pymavlink nunavut pkgconfig cerberus argcomplete pygments
  patch_px4_for_conda
  say "Building PX4 SITL with Gazebo support (10-20 minutes the first time)"
  (conda_env && cd "$PX4_DIR" && make px4_sitl_default)
  mkdir -p "$REPO/data/sim" && echo "$ENV" > "$REPO/data/sim/gz_env"
}

# ---------------------------------------------------------------- 2b. Ubuntu route
setup_ubuntu() {
  say "Installing PX4's build tools and Gazebo Harmonic (sudo)"
  bash "$PX4_DIR/Tools/setup/ubuntu.sh" --no-nuttx
  say "Installing Gazebo's Python bindings"
  sudo apt-get install -y python3-gz-transport13 python3-gz-msgs10
  say "Building PX4 SITL (10-20 minutes the first time)"
  (cd "$PX4_DIR" && make px4_sitl_default)
  rm -f "$REPO/data/sim/gz_env"
}

if [ -n "$USE_CONDA" ]; then setup_conda; else setup_ubuntu; fi

# ---------------------------------------------------------------- 3. STRIX-1 itself
say "Preparing STRIX-1"
cd "$REPO/backend"
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
if .venv/bin/python -m pip --version >/dev/null 2>&1 || .venv/bin/python -m ensurepip --upgrade >/dev/null 2>&1; then
  .venv/bin/python -m pip install -q -r requirements-dev.txt
elif command -v uv >/dev/null; then
  uv pip install -q --python .venv/bin/python -r requirements-dev.txt
else
  die "backend/.venv has no pip: delete backend/.venv and run this script again"
fi
cd "$REPO"
ls models/*.onnx >/dev/null 2>&1 || scripts/get_detector_model.sh || warn "YOLO model download failed; run scripts/get_detector_model.sh later"
if command -v npm >/dev/null; then (cd frontend && npm ci --no-audit --no-fund && npm run build); else warn "Node.js missing: install it to build the dashboard"; fi
if command -v cargo >/dev/null; then (cd hub && cargo build --release); else warn "Rust missing: install it from https://rustup.rs to build the hub"; fi

# ---------------------------------------------------------------- 4. people and cars (optional)
say "Downloading Gazebo Fuel models for the search area (optional)"
GZ="gz"; [ -n "$USE_CONDA" ] && GZ="$ENV/bin/gz"
grep -v '^\s*#' sim/worlds/strix_field.objects | cut -d'|' -f2 | xargs -I{} echo {} | sort -u | while read -r uri; do
  [ -n "$uri" ] || continue
  "$GZ" fuel download -u "$uri" >/dev/null 2>&1 || true  # it exits 0 even when the download fails: check the cache
  cache="$HOME/.gz/fuel/fuel.gazebosim.org/$(echo "$uri" | sed 's#.*/1.0/##' | tr '[:upper:]' '[:lower:]')"
  if [ -d "$cache" ]; then echo "  cached $uri"; else warn "could not download $uri (no internet?); the world works without it"; fi
done

mkdir -p data/sim && echo "$PX4_DIR" > data/sim/px4_dir
cat <<EOF

  Done. Start a simulated fleet of 3 drones:
      scripts/sim/run_fleet.sh
  No graphics card? Use: scripts/sim/run_fleet.sh 2 --light
  Tip: install QGroundControl (https://qgroundcontrol.com) to fly them.
EOF
