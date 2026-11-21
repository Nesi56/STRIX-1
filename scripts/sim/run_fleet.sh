#!/usr/bin/env bash
# A simulated STRIX-1 fleet: Gazebo world + N PX4 drones (SITL) + one STRIX-1 service per drone + the hub.
#
#   scripts/sim/run_fleet.sh               3 drones, Gazebo window open
#   scripts/sim/run_fleet.sh 2 --headless  2 drones, no Gazebo window (watch the cameras in the hub)
#   scripts/sim/run_fleet.sh --stop        stop everything a previous run started
#
# Options: --model x500_gimbal (camera on a gimbal; default) | x500_mono_cam (fixed camera)
#          --light  smaller, slower simulated cameras (640 px, 10 fps): for laptops without a graphics card
# Run scripts/sim/setup_px4_gz.sh once first. You fly the drones (QGroundControl, or PX4's console);
# STRIX-1 only reads their telemetry and points each camera gimbal. Logs: data/sim/logs/.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SIM="$REPO/data/sim"
LOGS="$SIM/logs"
PIDS="$SIM/pids"
WORLD=strix_field
HUB_PORT="${STRIX_SIM_HUB_PORT:-9100}"
NAMES=(alpha bravo charlie delta echo foxtrot golf hotel)
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

stop_all() {
  if [ -f "$PIDS" ]; then
    while read -r pid; do kill "$pid" 2>/dev/null || true; done < "$PIDS"
    sleep 1
    while read -r pid; do kill -9 "$pid" 2>/dev/null || true; done < "$PIDS"
    rm -f "$PIDS"
  fi
  pkill -f "gz sim.*${WORLD}.sdf" 2>/dev/null || true
  pkill -f "gz sim -g" 2>/dev/null || true
  pkill -f "px4_sitl_default/bin/px4 -i" 2>/dev/null || true
}

N=3; HEADLESS=""; LIGHT=""; MODEL="x500_gimbal"
while [ $# -gt 0 ]; do
  case "$1" in
    --stop) stop_all; say "Simulation stopped."; exit 0 ;;
    --headless) HEADLESS=1 ;;
    --light) LIGHT=1 ;;
    --model) MODEL="$2"; shift ;;
    [1-8]) N="$1" ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
  shift
done
[[ "$MODEL" =~ ^[a-z0-9_]+$ ]] || die "bad model name"

# --------------------------------------------------------------- checks
PX4_DIR="${PX4_DIR:-$(cat "$SIM/px4_dir" 2>/dev/null || echo "$HOME/PX4-Autopilot")}"
# Gazebo from the self-contained conda environment (non-Ubuntu setup), if that is how it was installed.
GZ_PREFIX="${STRIX_SIM_ENV:-$(cat "$SIM/gz_env" 2>/dev/null || true)}"
[ -n "$GZ_PREFIX" ] && [ -x "$GZ_PREFIX/bin/gz" ] && export PATH="$GZ_PREFIX/bin:$PATH"
PX4_BIN="$PX4_DIR/build/px4_sitl_default/bin/px4"
GZ_ENV="$PX4_DIR/build/px4_sitl_default/rootfs/gz_env.sh"
[ -x "$PX4_BIN" ] || die "PX4 SITL not built in $PX4_DIR: run scripts/sim/setup_px4_gz.sh first (or set PX4_DIR)"
command -v gz >/dev/null || die "Gazebo (gz) not found: run scripts/sim/setup_px4_gz.sh first"
AF="$(ls "$PX4_DIR/ROMFS/px4fmu_common/init.d-posix/airframes/" | sed -n "s/^\([0-9]*\)_gz_${MODEL}$/\1/p" | head -1)"
[ -n "$AF" ] || die "PX4 has no Gazebo airframe for model '$MODEL'"
PY="$REPO/backend/.venv/bin/python"
[ -x "$PY" ] || die "backend virtualenv missing: run scripts/sim/setup_px4_gz.sh"
"$PY" -c "import pymavlink" 2>/dev/null || die "pymavlink missing: $PY -m pip install -r backend/requirements-sim.txt"
GZ_PY="${STRIX_SIM_GZ_PYTHON:-}"
if [ -z "$GZ_PY" ]; then
  for c in ${GZ_PREFIX:+"$GZ_PREFIX/bin/python3"} /usr/bin/python3 "$(command -v python3)"; do
    if [ -x "$c" ] && "$c" -c "import gz.transport13, gz.msgs10" 2>/dev/null; then GZ_PY="$c"; break; fi
  done
fi
[ -n "$GZ_PY" ] || warn "no Python with Gazebo's bindings: cameras will be off (Ubuntu: sudo apt install python3-gz-transport13 python3-gz-msgs10; other Linux: rerun scripts/sim/setup_px4_gz.sh)"
HUB_BIN="$REPO/hub/target/release/strix-hub"
if [ ! -x "$HUB_BIN" ]; then
  command -v cargo >/dev/null || die "the hub is not built and Rust is missing: see https://rustup.rs"
  say "Building the hub"; (cd "$REPO/hub" && cargo build --release --quiet)
fi
[ -f "$REPO/frontend/dist/index.html" ] || { say "Building the dashboard"; (cd "$REPO/frontend" && npm ci --no-audit --no-fund >/dev/null && npm run build >/dev/null); }
ls "$REPO"/models/*.onnx >/dev/null 2>&1 || warn "no YOLO model in models/: STRIX-1 falls back to a people-only detector (run scripts/get_detector_model.sh)"

stop_all
mkdir -p "$LOGS" "$REPO/hub/secrets"
: > "$PIDS"
trap 'echo; say "Stopping the simulation"; stop_all' EXIT INT TERM
remember() { echo "$1" >> "$PIDS"; }

# --------------------------------------------------------------- Gazebo
set +u   # PX4's gz_env.sh extends variables that may be unset
# shellcheck disable=SC1090
source "$GZ_ENV"   # PX4's models, plugins and server config
set -u
export GZ_IP="${GZ_IP:-127.0.0.1}"  # keep Gazebo's transport on this computer
if [ -n "$LIGHT" ]; then
  # Without a GPU, rendering every drone's 1280 px, 30 fps camera on the CPU slows the whole simulation.
  # Use copies of the drone model whose cameras are half size at 10 fps (PX4's files stay untouched).
  LM="$SIM/models_light"; mkdir -p "$LM"
  cp -r "$PX4_GZ_MODELS/$MODEL" "$LM/"
  for sub in $(sed -n 's#.*<uri>model://\([A-Za-z0-9_]*\)</uri>.*#\1#p' "$PX4_GZ_MODELS/$MODEL/model.sdf"); do
    grep -q 'type="camera"' "$PX4_GZ_MODELS/$sub/model.sdf" 2>/dev/null && cp -r "$PX4_GZ_MODELS/$sub" "$LM/"
  done
  python3 - "$LM" <<'PY'
import pathlib, re, sys
for f in pathlib.Path(sys.argv[1]).glob("*/model.sdf"):
    s = f.read_text()
    def light(m):
        b = m.group(0)
        b = re.sub(r"<width>(\d+)</width>", lambda w: f"<width>{int(w.group(1)) // 2}</width>", b)
        b = re.sub(r"<height>(\d+)</height>", lambda h: f"<height>{int(h.group(1)) // 2}</height>", b)
        return re.sub(r"<update_rate>[\d.]+</update_rate>", "<update_rate>10</update_rate>", b)
    f.write_text(re.sub(r'<sensor[^>]*type="camera".*?</sensor>', light, s, flags=re.S))
PY
  export GZ_SIM_RESOURCE_PATH="$LM:$GZ_SIM_RESOURCE_PATH" PX4_GZ_MODELS="$LM"
  say "Light cameras: half size, 10 fps"
fi
RENDER=""
[ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && RENDER="--headless-rendering"
say "Starting Gazebo world '$WORLD'"
gz sim -s -r $RENDER "$REPO/sim/worlds/$WORLD.sdf" > "$LOGS/gazebo.log" 2>&1 &
remember $!
for _ in $(seq 60); do gz topic -l 2>/dev/null | grep -q "^/world/$WORLD/clock" && break; sleep 1; done
gz topic -l 2>/dev/null | grep -q "^/world/$WORLD/clock" || die "Gazebo did not start (see $LOGS/gazebo.log)"
if [ -z "$HEADLESS" ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
  gz sim -g > "$LOGS/gazebo-gui.log" 2>&1 & remember $!
fi

# People, cars and cones (downloaded once from Gazebo Fuel). Failures only skip that object.
(
  fails=0
  grep -v '^\s*#' "$REPO/sim/worlds/$WORLD.objects" | while IFS='|' read -r name uri xyz yaw; do
    name="$(echo "$name" | xargs)"; uri="$(echo "$uri" | xargs)"; [ -n "$name" ] || continue
    read -r x y z <<< "$xyz"; yaw="$(echo "$yaw" | xargs)"
    qz="$(awk -v a="$yaw" 'BEGIN{printf "%.4f", sin(a/2)}')"; qw="$(awk -v a="$yaw" 'BEGIN{printf "%.4f", cos(a/2)}')"
    req="sdf: \"<sdf version='1.9'><include><uri>$uri</uri><name>$name</name></include></sdf>\", pose: {position: {x: $x, y: $y, z: $z}, orientation: {z: $qz, w: $qw}}"
    gz service -s "/world/$WORLD/create" --reqtype gz.msgs.EntityFactory --reptype gz.msgs.Boolean \
      --timeout 60000 --req "$req" >/dev/null 2>&1 || true
    # The service only queues the request: check the model really appeared (it needs a download the first time).
    ok=""
    for _ in $(seq 60); do gz model --list 2>/dev/null | grep -q -- "- $name\$" && { ok=1; break; }; sleep 1; done
    if [ -n "$ok" ]; then echo "added $name"; fails=0
    else
      echo "SKIPPED $name: not in the world after 60 s (no internet for Gazebo Fuel? $uri)"
      fails=$((fails + 1)); [ "$fails" -ge 2 ] && { echo "two downloads failed in a row: skipping the rest"; break; }
    fi
  done
) > "$LOGS/objects.log" 2>&1 &
remember $!

# --------------------------------------------------------------- PX4 drones
for i in $(seq 1 "$N"); do
  y=$(( (i - 1) * 4 - (N - 1) * 2 ))
  say "Starting PX4 drone $i (${NAMES[$((i-1))]}) at 0, $y"
  ( cd "$PX4_DIR" && PX4_SYS_AUTOSTART="$AF" PX4_SIM_MODEL="gz_$MODEL" PX4_GZ_WORLD="$WORLD" \
      PX4_GZ_MODEL_POSE="0,$y" exec "$PX4_BIN" -i "$i" -d ) > "$LOGS/px4_$i.log" 2>&1 &
  remember $!
  for _ in $(seq 45); do grep -q "Startup script returned successfully\|ERROR \[init\]" "$LOGS/px4_$i.log" 2>/dev/null && break; sleep 1; done
  grep -q "Startup script returned successfully" "$LOGS/px4_$i.log" || warn "PX4 drone $i may not have started (see $LOGS/px4_$i.log)"
done

# --------------------------------------------------------------- STRIX-1 services + hub
CONF="$REPO/hub/sim.toml"
{
  echo "# Written by scripts/sim/run_fleet.sh for the simulated fleet."
  echo "listen = \"127.0.0.1:$HUB_PORT\""
  echo 'ui_dir = "../frontend/dist"'
  echo 'data_dir = "data"   # same sign-in (PIN / token) as your real hub'
} > "$CONF"
for i in $(seq 1 "$N"); do
  id="${NAMES[$((i-1))]}"; port=$((8100 + i))
  token="$(head -c 18 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  (umask 077; echo "$token" > "$REPO/hub/secrets/sim_$id.token")
  printf '\n[[drone]]\nid = "%s"\nname = "%s"\nurl = "http://127.0.0.1:%s"\ntoken_file = "secrets/sim_%s.token"\n' \
    "$id" "$(tr '[:lower:]' '[:upper:]' <<< "${id:0:1}")${id:1}" "$port" "$id" >> "$CONF"
  say "Starting STRIX-1 for drone $i on port $port"
  ( cd "$REPO/backend" && STRIX_SIM=px4-gz STRIX_SIM_INSTANCE="$i" STRIX_SIM_MODEL="$MODEL" STRIX_SIM_GZ_PYTHON="$GZ_PY" \
      STRIX_HOST=127.0.0.1 STRIX_PORT="$port" STRIX_DATA_DIR="$SIM/drone$i" STRIX_AUTH_TOKEN="$token" \
      STRIX_MOCK=false QWEN_MANAGE_SERVER=false exec "$PY" -m app.main ) > "$LOGS/strix_$i.log" 2>&1 &
  remember $!
done
say "Starting the hub on port $HUB_PORT"
"$HUB_BIN" "$CONF" > "$LOGS/hub.log" 2>&1 &
remember $!
sleep 3

cat <<EOF

  Simulated fleet is running: $N drone(s), world '$WORLD'.

  Dashboard:  http://127.0.0.1:$HUB_PORT   (sign in with your hub PIN; token: hub/target/release/strix-hub token hub/sim.toml)
  Fly them:   QGroundControl finds every drone by itself (UDP 14550). Or PX4's console, e.g.
              $PX4_DIR/build/px4_sitl_default/bin/px4-commander --instance 1 takeoff
  You fly. STRIX-1 only reads the telemetry and points each camera gimbal.
  Logs:       $LOGS     Stop: Ctrl+C here, or scripts/sim/run_fleet.sh --stop

EOF
wait
