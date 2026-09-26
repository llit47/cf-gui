#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/cf-gui
ENV_FILE=/etc/cf-gui.env
SERVICE_FILE=/etc/systemd/system/cf-gui.service
REPO_URL=https://github.com/llit47/cf-gui.git

LOG_FILE=$(mktemp /tmp/cf-gui-install.XXXXXX.log)
STEP_NAME='Installer setup'
STEP_OPEN=0
GREEN='' RED='' YELLOW='' RESET=''
if [[ -t 1 && -z ${NO_COLOR+x} ]]; then
  GREEN=$'\033[32m'
  RED=$'\033[31m'
  YELLOW=$'\033[33m'
  RESET=$'\033[0m'
fi

finish() {
  local status=$?
  if (( status == 0 )); then
    rm -f "$LOG_FILE" || true
    return
  fi
  if (( STEP_OPEN )); then
    printf '%s[FAIL]%s\n' "$RED" "$RESET" >&2
  fi
  printf '\nInstallation failed during:\n  %s\n\nRecent command output:\n' "$STEP_NAME" >&2
  printf '%s\n' '----------------------------------------' >&2
  if [[ -s $LOG_FILE ]]; then
    tail -n 30 "$LOG_FILE" >&2 || true
  else
    printf '%s\n' '(No command output captured.)' >&2
  fi
  printf '%s\n' '----------------------------------------' >&2
  if [[ -e $LOG_FILE ]]; then
    printf 'Full log: %s\n' "$LOG_FILE" >&2
  fi
}
trap finish EXIT

begin_step() {
  STEP_NAME=$2
  STEP_OPEN=1
  printf '[%s/7] %-36s ' "$1" "$STEP_NAME"
  printf '\n=== %s ===\n' "$STEP_NAME" >> "$LOG_FILE"
}

end_step() {
  printf '%s[OK]%s\n' "$GREEN" "$RESET"
  STEP_OPEN=0
}

local_ipv4() {
  local addresses address fallback=''
  addresses=$(hostname -I 2>> "$LOG_FILE") || return 1
  for address in $addresses; do
    [[ $address =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
    [[ $address == 127.* || $address == 169.254.* || $address == 0.* ]] && continue
    if [[ $address == 10.* || $address == 192.168.* || $address =~ ^172\.(1[6-9]|2[0-9]|3[01])\. ]]; then
      printf '%s' "$address"
      return 0
    fi
    [[ -n $fallback ]] || fallback=$address
  done
  [[ -n $fallback ]] || return 1
  printf '%s' "$fallback"
}

printf 'cf-gui installer\n%s\n\n' '----------------------------------------'

begin_step 1 'Checking system'
if [[ $EUID -ne 0 ]]; then
  printf '%s\n' 'Run this installer as root.' >> "$LOG_FILE"
  exit 1
fi
if [[ -e $APP_DIR || -e $SERVICE_FILE || -e $ENV_FILE ]]; then
  printf '%s\n' 'An existing cf-gui installation was found. Use cf-gui-update to update it.' >> "$LOG_FILE"
  exit 1
fi
CONFIG_PATH=${CF_GUI_CONFIG_PATH:-/etc/cloudflared/config.yml}
START_PORT=${CF_GUI_PORT:-8000}
if [[ $CONFIG_PATH == *$'\n'* || $CONFIG_PATH == *$'\r'* || ! $START_PORT =~ ^[0-9]+$ ]] || (( START_PORT < 1 || START_PORT > 65535 )); then
  printf '%s\n' 'Invalid CF_GUI_CONFIG_PATH or CF_GUI_PORT.' >> "$LOG_FILE"
  exit 1
fi
end_step

begin_step 2 'Installing dependencies'
export DEBIAN_FRONTEND=noninteractive
apt-get update >> "$LOG_FILE" 2>&1
apt-get install -y --no-install-recommends ca-certificates git python3 python3-venv python3-pip >> "$LOG_FILE" 2>&1
end_step

begin_step 3 'Downloading cf-gui'
git clone --depth 1 --branch main "$REPO_URL" "$APP_DIR" >> "$LOG_FILE" 2>&1
end_step

begin_step 4 'Creating Python environment'
python3 -m venv "$APP_DIR/.venv" >> "$LOG_FILE" 2>&1
"$APP_DIR/.venv/bin/pip" install --no-cache-dir "$APP_DIR" >> "$LOG_FILE" 2>&1
end_step

begin_step 5 'Creating configuration'
# Keep generated credentials out of the command log; only their hash/secret go to the root-only env file.
ADMIN_PASSWORD=$("$APP_DIR/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(18))' 2>> "$LOG_FILE")
PASSWORD_HASH=$("$APP_DIR/.venv/bin/python" -c 'import sys; from werkzeug.security import generate_password_hash; print(generate_password_hash(sys.argv[1]))' "$ADMIN_PASSWORD" 2>> "$LOG_FILE")
SECRET_KEY=$("$APP_DIR/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))' 2>> "$LOG_FILE")
umask 077
cat 2>> "$LOG_FILE" > "$ENV_FILE" <<ENVEOF
CF_GUI_CONFIG_PATH=$CONFIG_PATH
CF_GUI_PORT=$START_PORT
CF_GUI_PASSWORD_HASH=$PASSWORD_HASH
CF_GUI_SECRET_KEY=$SECRET_KEY
ENVEOF
chmod 600 "$ENV_FILE" >> "$LOG_FILE" 2>&1
end_step

begin_step 6 'Installing systemd service'
cat 2>> "$LOG_FILE" > "$SERVICE_FILE" <<'SERVICEEOF'
[Unit]
Description=cf-gui Cloudflare Tunnel manager
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/cf-gui
EnvironmentFile=/etc/cf-gui.env
ExecStart=/opt/cf-gui/.venv/bin/python -m cf_gui
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
SERVICEEOF

cat 2>> "$LOG_FILE" > /usr/local/bin/cf-gui-update <<'UPDATEEOF'
#!/usr/bin/env bash
exec /opt/cf-gui/scripts/update.sh "$@"
UPDATEEOF
chmod 755 /usr/local/bin/cf-gui-update >> "$LOG_FILE" 2>&1
systemctl daemon-reload >> "$LOG_FILE" 2>&1
end_step

begin_step 7 'Starting cf-gui'
if ! systemctl enable --now cf-gui.service >> "$LOG_FILE" 2>&1; then
  systemctl status cf-gui.service --no-pager -l >> "$LOG_FILE" 2>&1 || true
  journalctl -u cf-gui.service -n 50 --no-pager >> "$LOG_FILE" 2>&1 || true
  exit 1
fi
sleep 2
if ! systemctl is-active --quiet cf-gui.service >> "$LOG_FILE" 2>&1; then
  systemctl status cf-gui.service --no-pager -l >> "$LOG_FILE" 2>&1 || true
  journalctl -u cf-gui.service -n 50 --no-pager >> "$LOG_FILE" 2>&1 || true
  exit 1
fi
end_step

PORT_LINE=$(journalctl -u cf-gui.service -n 30 -o cat --no-pager 2>> "$LOG_FILE" | grep -F 'cf-gui listening on ' | tail -n 1 || true)
if [[ $PORT_LINE =~ cf-gui[[:space:]]listening[[:space:]]on[[:space:]]0\.0\.0\.0:([0-9]+) ]]; then
  WEB_PORT=${BASH_REMATCH[1]}
else
  WEB_PORT='<port>'
fi
WEB_IP=$(local_ipv4 || true)
WEB_IP=${WEB_IP:-'<server-ip>'}

STEP_NAME='Final summary'
printf '\nInstallation complete.\n\nWeb interface:\n  http://%s:%s\n\n' "$WEB_IP" "$WEB_PORT"
if [[ $WEB_PORT == '<port>' ]]; then
  printf '%s[WARN]%s Check the listening port with: journalctl -u cf-gui.service -n 30 --no-pager\n\n' "$YELLOW" "$RESET"
fi
if [[ $WEB_IP == '<server-ip>' ]]; then
  printf '%s[WARN]%s Replace <server-ip> with the IPv4 address of this server.\n\n' "$YELLOW" "$RESET"
fi
printf 'Login:\n  User:     admin\n  Password: %s\n\n' "$ADMIN_PASSWORD"
printf 'Cloudflared config:\n  %s\n\n' "$CONFIG_PATH"
printf 'Service:\n  systemctl status cf-gui\n\nUpdate:\n  cf-gui-update\n\n'
printf '%s\n' 'IMPORTANT: Save the admin password now.' 'It is not stored in plaintext.'
