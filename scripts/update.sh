#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/cf-gui
if [[ $EUID -ne 0 ]]; then
  echo 'Uruchom cf-gui-update jako root.' >&2
  exit 1
fi
if [[ ! -d "$APP_DIR/.git" || ! -x "$APP_DIR/.venv/bin/pip" ]]; then
  echo 'Nie znaleziono instalacji cf-gui w /opt/cf-gui.' >&2
  exit 1
fi
if [[ -n $(git -C "$APP_DIR" status --porcelain --untracked-files=no) ]]; then
  echo 'W /opt/cf-gui są lokalne zmiany. Zapisz je przed aktualizacją.' >&2
  exit 1
fi

git -C "$APP_DIR" pull --ff-only origin main
"$APP_DIR/.venv/bin/pip" install --no-cache-dir --upgrade "$APP_DIR"
systemctl restart cf-gui.service
sleep 2
if ! systemctl is-active --quiet cf-gui.service; then
  echo 'cf-gui nie wystartowało po aktualizacji. Sprawdź: journalctl -u cf-gui -n 50 --no-pager' >&2
  exit 1
fi
PORT_LINE=$(journalctl -u cf-gui.service -n 30 -o cat --no-pager 2>/dev/null | grep 'cf-gui listening on ' | tail -n 1 || true)
echo "cf-gui zaktualizowane. ${PORT_LINE:-Sprawdź port w journalctl -u cf-gui -n 30 --no-pager}"
