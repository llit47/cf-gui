#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/cf-gui
ENV_FILE=/etc/cf-gui.env
SERVICE_FILE=/etc/systemd/system/cf-gui.service
REPO_URL=https://github.com/llit47/cf-gui.git

if [[ $EUID -ne 0 ]]; then
  echo 'Uruchom installer jako root.' >&2
  exit 1
fi
if [[ -e "$APP_DIR" || -e "$SERVICE_FILE" || -e "$ENV_FILE" ]]; then
  echo 'cf-gui jest już zainstalowane lub istnieje jego katalog. Użyj cf-gui-update albo sprawdź istniejącą instalację.' >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates git python3 python3-venv python3-pip

git clone --depth 1 --branch main "$REPO_URL" "$APP_DIR"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --no-cache-dir "$APP_DIR"

# Generated password is printed once; only its hash is stored.
ADMIN_PASSWORD=$("$APP_DIR/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(18))')
PASSWORD_HASH=$("$APP_DIR/.venv/bin/python" -c 'import sys; from werkzeug.security import generate_password_hash; print(generate_password_hash(sys.argv[1]))' "$ADMIN_PASSWORD")
SECRET_KEY=$("$APP_DIR/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')
CONFIG_PATH=${CF_GUI_CONFIG_PATH:-/etc/cloudflared/config.yml}
START_PORT=${CF_GUI_PORT:-8000}
if [[ $CONFIG_PATH == *$'\n'* || $CONFIG_PATH == *$'\r'* || ! $START_PORT =~ ^[0-9]+$ ]]; then
  echo 'Nieprawidłowe CF_GUI_CONFIG_PATH lub CF_GUI_PORT.' >&2
  exit 1
fi

umask 077
cat > "$ENV_FILE" <<ENVEOF
CF_GUI_CONFIG_PATH=$CONFIG_PATH
CF_GUI_PORT=$START_PORT
CF_GUI_PASSWORD_HASH=$PASSWORD_HASH
CF_GUI_SECRET_KEY=$SECRET_KEY
ENVEOF
chmod 600 "$ENV_FILE"

cat > "$SERVICE_FILE" <<'SERVICEEOF'
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

cat > /usr/local/bin/cf-gui-update <<'UPDATEEOF'
#!/usr/bin/env bash
exec /opt/cf-gui/scripts/update.sh "$@"
UPDATEEOF
chmod 755 /usr/local/bin/cf-gui-update
systemctl daemon-reload
systemctl enable --now cf-gui.service
sleep 2
if ! systemctl is-active --quiet cf-gui.service; then
  echo 'Usługa cf-gui nie wystartowała. Sprawdź: journalctl -u cf-gui -n 50 --no-pager' >&2
  exit 1
fi
PORT_LINE=$(journalctl -u cf-gui.service -n 30 -o cat --no-pager 2>/dev/null | grep 'cf-gui listening on ' | tail -n 1 || true)
echo "Hasło admina (zapisz teraz): $ADMIN_PASSWORD"
echo "${PORT_LINE:-Port sprawdź poleceniem: journalctl -u cf-gui -n 30 --no-pager}"
echo 'Aktualizacja: cf-gui-update'
