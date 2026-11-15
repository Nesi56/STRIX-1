#!/usr/bin/env bash
# Host the STRIX-1 hub on this computer (Linux or macOS) in one command:
# builds it, asks for your drones and a PIN the first time, and makes it start by itself.
#
#   scripts/host_hub.sh              set up (or update) and start
#   scripts/host_hub.sh --no-service build and configure only; run it yourself
#   scripts/host_hub.sh --stop       stop the hub and remove the autostart
#
# Safe to re-run after `git pull`: hub.toml, secrets/ and the PIN are kept.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HUB="$REPO/hub"
BIN="$HUB/target/release/strix-hub"
OS="$(uname -s)"
UNIT="$HOME/.config/systemd/user/strix-hub.service"
PLIST="$HOME/Library/LaunchAgents/com.strix1.hub.plist"
say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

stop_service() {
  if [ "$OS" = Linux ] && [ -f "$UNIT" ]; then
    systemctl --user disable --now strix-hub 2>/dev/null || true
    rm -f "$UNIT"; systemctl --user daemon-reload 2>/dev/null || true
  elif [ "$OS" = Darwin ] && [ -f "$PLIST" ]; then
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
  fi
}

case "${1:-}" in
  --stop) stop_service; say "Hub stopped and autostart removed."; exit 0 ;;
  --no-service|"") ;;
  *) die "unknown option $1 (use --no-service or --stop)" ;;
esac

command -v cargo >/dev/null || die "Rust is not installed: see https://rustup.rs"
command -v npm >/dev/null || die "Node.js 20+ is not installed: see https://nodejs.org"

# Stop a running copy so the rebuilt binary can replace it.
if [ "$OS" = Linux ] && [ -f "$UNIT" ]; then systemctl --user stop strix-hub 2>/dev/null || true; fi
if [ "$OS" = Darwin ] && [ -f "$PLIST" ]; then launchctl unload "$PLIST" 2>/dev/null || true; fi

say "Building the dashboard"
(cd "$REPO/frontend" && npm ci --no-audit --no-fund && npm run build) >/dev/null
say "Building the hub"
(cd "$HUB" && cargo build --release --quiet)

cd "$HUB"
if [ ! -f hub.toml ]; then
  say "First-time setup: your drones"
  mkdir -p secrets && chmod 700 secrets
  {
    echo '# STRIX-1 hub, written by scripts/host_hub.sh. Edit freely; tokens live in secrets/.'
    echo 'listen = "0.0.0.0:9000"'
    echo 'ui_dir = "../frontend/dist"'
    echo 'data_dir = "data"'
  } > hub.toml
  read -rp "How many drones? [2] " count; count="${count:-2}"
  [[ "$count" =~ ^[1-8]$ ]] || die "use a number from 1 to 8"
  names=(alpha bravo charlie delta echo foxtrot golf hotel)
  for ((i = 0; i < count; i++)); do
    id="${names[$i]}"
    echo; say "Drone $((i + 1)) ($id)"
    read -rp "  Address of its Pi (e.g. 192.168.1.21 or strix-$id.local): " host
    [ -n "$host" ] || die "an address is needed"
    case "$host" in
      http://*|https://*) url="$host" ;;
      *:*) url="http://$host" ;;            # already has a port
      *) url="http://$host:8000" ;;
    esac
    read -rsp "  Its STRIX_AUTH_TOKEN (on the Pi: grep STRIX_AUTH_TOKEN ~/STRIX-1/.env): " tok; echo
    [ -n "$tok" ] || die "a token is needed"
    (umask 077; printf '%s\n' "$tok" > "secrets/$id.token")
    printf '\n[[drone]]\nid = "%s"\nname = "%s"\nurl = "%s"\ntoken_file = "secrets/%s.token"\n' \
      "$id" "$(tr '[:lower:]' '[:upper:]' <<< "${id:0:1}")${id:1}" "$url" "$id" >> hub.toml
  done
else
  say "Keeping your existing hub.toml"
fi

if [ ! -s data/hub_pin ]; then
  echo; say "Choose the operator PIN you will sign in with"
  "$BIN" set-pin hub.toml
fi

if [ "${1:-}" = --no-service ]; then
  say "Done. Start it with:  cd $HUB && $BIN hub.toml"
elif [ "$OS" = Linux ]; then
  command -v systemctl >/dev/null || die "no systemd here: use --no-service and start it yourself"
  mkdir -p "$(dirname "$UNIT")"
  cat > "$UNIT" <<EOF
[Unit]
Description=STRIX-1 hub
After=network-online.target

[Service]
WorkingDirectory=$HUB
ExecStart=$BIN hub.toml
Restart=on-failure

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now strix-hub
  loginctl enable-linger "$USER" 2>/dev/null || true
  say "Running. Logs: journalctl --user -u strix-hub -f"
elif [ "$OS" = Darwin ]; then
  mkdir -p "$(dirname "$PLIST")" data
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.strix1.hub</string>
  <key>WorkingDirectory</key><string>$HUB</string>
  <key>ProgramArguments</key><array><string>$BIN</string><string>hub.toml</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>$HUB/data/hub.log</string>
</dict></plist>
EOF
  launchctl load -w "$PLIST"
  say "Running. Log: $HUB/data/hub.log"
else
  die "unsupported system $OS: on Windows follow docs/HUB.md"
fi

ip=""
if [ "$OS" = Darwin ]; then ip="$(ipconfig getifaddr en0 2>/dev/null || true)"; else ip="$(hostname -I 2>/dev/null | awk '{print $1}')"; fi
echo
say "Open http://127.0.0.1:9000 on this computer${ip:+, or http://$ip:9000 from a phone on the same Wi-Fi}."
say "Sign in with your PIN. Recovery token: $BIN token hub.toml"
