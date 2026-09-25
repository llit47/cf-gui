#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/cf-gui
STATE_DIR=/var/lib/cf-gui
INSTALLED_COMMIT_FILE=$STATE_DIR/installed-commit
echo 'cf-gui updater'

if [[ $EUID -ne 0 ]]; then
  echo 'Uruchom cf-gui-update jako root.' >&2
  exit 1
fi
if [[ ! -d "$APP_DIR/.git" || ! -x "$APP_DIR/.venv/bin/pip" ]]; then
  echo 'Nie znaleziono instalacji cf-gui w /opt/cf-gui.' >&2
  exit 1
fi
if ! LOCAL_CHANGES=$(git -C "$APP_DIR" status --porcelain --untracked-files=no 2>&1); then
  echo 'Nie można sprawdzić lokalnych zmian w /opt/cf-gui.' >&2
  printf '%s\n' "$LOCAL_CHANGES" >&2
  exit 1
fi
if [[ -n $LOCAL_CHANGES ]]; then
  echo 'W /opt/cf-gui są lokalne zmiany. Zapisz je przed aktualizacją.' >&2
  printf '%s\n' "$LOCAL_CHANGES" >&2
  exit 1
fi
CURRENT_BRANCH=$(git -C "$APP_DIR" branch --show-current)
if [[ $CURRENT_BRANCH != main ]]; then
  echo 'Aktualizacja wymaga aktywnej gałęzi main w /opt/cf-gui.' >&2
  exit 1
fi

run_quiet() {
  local step=$1 output
  shift
  if ! output=$("$@" 2>&1); then
    printf '%s: BŁĄD\n' "$step" >&2
    [[ -z $output ]] || printf '%s\n' "$output" >&2
    exit 1
  fi
}

OLD_COMMIT=$(git -C "$APP_DIR" rev-parse HEAD)
run_quiet 'Sprawdzanie aktualizacji' git -C "$APP_DIR" fetch --quiet origin main
NEW_COMMIT=$(git -C "$APP_DIR" rev-parse refs/remotes/origin/main)
echo 'Sprawdzanie aktualizacji... OK'

if [[ $OLD_COMMIT == "$NEW_COMMIT" ]]; then
  INSTALLED_COMMIT=$(cat "$INSTALLED_COMMIT_FILE" 2>/dev/null || true)
  if [[ $INSTALLED_COMMIT == "$NEW_COMMIT" ]]; then
    echo 'cf-gui jest już aktualne.'
    exit 0
  fi
else
  if ! git -C "$APP_DIR" merge-base --is-ancestor "$OLD_COMMIT" "$NEW_COMMIT"; then
    echo 'Lokalny main nie może zostać zaktualizowany fast-forward do origin/main.' >&2
    exit 1
  fi
  run_quiet 'Aktualizacja' git -C "$APP_DIR" merge --ff-only origin/main
  printf 'Aktualizacja: %s -> %s\n' "${OLD_COMMIT:0:7}" "${NEW_COMMIT:0:7}"
fi

run_quiet 'Instalacja' "$APP_DIR/.venv/bin/pip" install --no-cache-dir --upgrade "$APP_DIR"
echo 'Instalacja... OK'

show_service_diagnostics() {
  systemctl status cf-gui.service --no-pager -l >&2 || true
  journalctl -u cf-gui.service -n 50 --no-pager >&2 || true
}
if ! RESTART_OUTPUT=$(systemctl restart cf-gui.service 2>&1); then
  echo 'Restart usługi... BŁĄD' >&2
  [[ -z $RESTART_OUTPUT ]] || printf '%s\n' "$RESTART_OUTPUT" >&2
  show_service_diagnostics
  exit 1
fi
sleep 2
if ! ACTIVE_OUTPUT=$(systemctl is-active cf-gui.service 2>&1); then
  echo 'Restart usługi... BŁĄD' >&2
  [[ -z $ACTIVE_OUTPUT ]] || printf '%s\n' "$ACTIVE_OUTPUT" >&2
  show_service_diagnostics
  exit 1
fi
echo 'Restart usługi... OK'
mkdir -p "$STATE_DIR"
MARKER_TMP=$(mktemp "$STATE_DIR/.installed-commit.XXXXXX")
trap 'rm -f "$MARKER_TMP"' EXIT
printf '%s\n' "$NEW_COMMIT" > "$MARKER_TMP"
mv -f "$MARKER_TMP" "$INSTALLED_COMMIT_FILE"
PORT_LINE=$(journalctl -u cf-gui.service -n 30 -o cat --no-pager 2>/dev/null | grep 'cf-gui listening on ' | tail -n 1 || true)
echo "Gotowe. ${PORT_LINE:-Port sprawdź poleceniem: journalctl -u cf-gui -n 30 --no-pager}"
