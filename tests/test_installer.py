"""Exercise installer output with local command stubs, never apt or systemd."""

import errno
import os
import pty
import re
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/install.sh"


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def _installer(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    app_dir = tmp_path / "app"
    env_file = tmp_path / "cf-gui.env"
    service_file = tmp_path / "systemd/cf-gui.service"
    service_file.parent.mkdir()
    command_dir = tmp_path / "bin"
    command_dir.mkdir()

    source = SCRIPT.read_text()

    def replace_once(old: str, new: str) -> None:
        nonlocal source
        assert source.count(old) == 1, f"Installer test isolation needs updating: {old}"
        source = source.replace(old, new, 1)

    replace_once("APP_DIR=/opt/cf-gui", f"APP_DIR={app_dir}")
    replace_once("ENV_FILE=/etc/cf-gui.env", f"ENV_FILE={env_file}")
    replace_once("SERVICE_FILE=/etc/systemd/system/cf-gui.service", f"SERVICE_FILE={service_file}")
    replace_once("/tmp/cf-gui-install.XXXXXX.log", str(tmp_path / "install.XXXXXX.log"))
    replace_once("> /usr/local/bin/cf-gui-update", f"> {tmp_path / 'cf-gui-update'}")
    replace_once("chmod 755 /usr/local/bin/cf-gui-update", f"chmod 755 {tmp_path / 'cf-gui-update'}")
    replace_once("[[ $EUID -ne 0 ]]", "[[ 0 -ne 0 ]]")
    script = tmp_path / "install.sh"
    script.write_text(source)

    _stub(command_dir / "apt-get", "echo 'ordinary apt output'\n")
    _stub(command_dir / "git", 'mkdir -p "$MOCK_APP_DIR"\necho "ordinary git output"\n')
    _stub(
        command_dir / "python3",
        'mkdir -p "$MOCK_APP_DIR/.venv/bin"\n'
        'cp "$MOCK_BIN/pip" "$MOCK_APP_DIR/.venv/bin/pip"\n'
        'cp "$MOCK_BIN/venv-python" "$MOCK_APP_DIR/.venv/bin/python"\n'
        'echo "ordinary venv output"\n',
    )
    _stub(
        command_dir / "pip",
        'echo "ordinary pip output"\n'
        'if [ "${MOCK_FAIL:-}" = pip ]; then echo "pip install failed: example diagnostic" >&2; exit 23; fi\n',
    )
    _stub(
        command_dir / "venv-python",
        'case "$2" in\n'
        '  *token_urlsafe\\(18\\)*) echo TEST_ADMIN_PASSWORD ;;\n'
        '  *generate_password_hash*) echo TEST_PASSWORD_HASH ;;\n'
        '  *token_urlsafe\\(48\\)*) echo TEST_SECRET_KEY ;;\n'
        'esac\n',
    )
    _stub(
        command_dir / "systemctl",
        'echo "ordinary systemctl output"\n'
        'if [ "$1" = enable ] && [ "${MOCK_FAIL:-}" = start ]; then '
        'echo "service start failed: example diagnostic" >&2; exit 1; fi\n'
        'if [ "$1" = is-active ] && [ "${MOCK_FAIL:-}" = active ]; then '
        'echo "service is inactive: example diagnostic" >&2; exit 1; fi\n',
    )
    _stub(command_dir / "journalctl", 'echo "cf-gui listening on 0.0.0.0:8001"\n')
    _stub(command_dir / "hostname", 'echo "127.0.0.1 192.0.2.9"\n')
    _stub(command_dir / "sleep", "exit 0\n")

    env = os.environ.copy()
    env.update(PATH=f"{command_dir}:{env['PATH']}", MOCK_APP_DIR=str(app_dir), MOCK_BIN=str(command_dir))
    return script, env


def test_installer_success_is_compact_and_shows_password_once(tmp_path):
    script, env = _installer(tmp_path)
    result = subprocess.run(["bash", str(script)], env=env, text=True, capture_output=True, check=False, timeout=10)

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("[OK]") == 7
    assert "ordinary apt output" not in result.stdout
    assert "ordinary pip output" not in result.stdout
    assert "http://192.0.2.9:8001" in result.stdout
    assert (result.stdout + result.stderr).count("TEST_ADMIN_PASSWORD") == 1
    assert "cf-gui-update" in result.stdout
    assert result.stdout.isascii()
    assert "\x1b" not in result.stdout + result.stderr
    assert not list(tmp_path.glob("install.*.log"))


@pytest.mark.parametrize(
    ("failure", "diagnostic"),
    [("start", "service start failed: example diagnostic"), ("active", "service is inactive: example diagnostic")],
)
def test_installer_failure_keeps_diagnostics_without_secrets(tmp_path, failure, diagnostic):
    script, env = _installer(tmp_path)
    env["MOCK_FAIL"] = failure
    result = subprocess.run(["bash", str(script)], env=env, text=True, capture_output=True, check=False, timeout=10)

    assert result.returncode != 0
    assert "[7/7] Starting cf-gui" in result.stdout
    assert "[FAIL]" in result.stderr
    assert diagnostic in result.stderr
    assert "Installation failed during:\n  Starting cf-gui" in result.stderr
    assert "Admin credentials were already created." in result.stderr
    assert "Password: TEST_ADMIN_PASSWORD" in result.stderr
    assert (result.stdout + result.stderr).count("TEST_ADMIN_PASSWORD") == 1
    log_path = Path(re.search(r"Full log: (.+)", result.stderr).group(1))
    assert log_path.is_file()
    log = log_path.read_text()
    assert "ordinary pip output" in log
    assert "TEST_ADMIN_PASSWORD" not in log
    assert "TEST_ADMIN_PASSWORD" not in (tmp_path / "cf-gui.env").read_text()
    assert "TEST_PASSWORD_HASH" not in log + result.stdout + result.stderr
    assert "TEST_SECRET_KEY" not in log + result.stdout + result.stderr


def test_existing_installation_points_to_updater(tmp_path):
    script, env = _installer(tmp_path)
    (tmp_path / "app").mkdir()
    result = subprocess.run(["bash", str(script)], env=env, text=True, capture_output=True, check=False, timeout=10)

    assert result.returncode != 0
    assert "[1/7] Checking system" in result.stdout
    assert "cf-gui-update" in result.stderr
    assert "ordinary apt output" not in result.stdout + result.stderr
    assert "TEST_ADMIN_PASSWORD" not in result.stdout + result.stderr


def test_failed_pip_reports_the_python_environment_step(tmp_path):
    script, env = _installer(tmp_path)
    env["MOCK_FAIL"] = "pip"
    result = subprocess.run(["bash", str(script)], env=env, text=True, capture_output=True, check=False, timeout=10)

    assert result.returncode == 23
    assert "Installation failed during:\n  Creating Python environment" in result.stderr
    assert "pip install failed: example diagnostic" in result.stderr
    assert "TEST_ADMIN_PASSWORD" not in result.stdout + result.stderr
    assert "Admin credentials were already created." not in result.stderr


def test_missing_ip_and_port_show_clear_placeholders(tmp_path):
    script, env = _installer(tmp_path)
    _stub(tmp_path / "bin/hostname", "exit 1\n")
    _stub(tmp_path / "bin/journalctl", "exit 1\n")
    result = subprocess.run(["bash", str(script)], env=env, text=True, capture_output=True, check=False, timeout=10)

    assert result.returncode == 0, result.stderr
    assert "http://<server-ip>:<port>" in result.stdout
    assert result.stdout.count("[WARN]") == 2
    assert "TEST_ADMIN_PASSWORD" in result.stdout


def test_no_color_is_respected_even_with_tty(tmp_path):
    script, env = _installer(tmp_path)
    env["CF_GUI_PORT"] = "invalid"
    env["NO_COLOR"] = ""
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(["bash", str(script)], env=env, stdout=slave, stderr=subprocess.PIPE)
        os.close(slave)
        output = bytearray()
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            output.extend(chunk)
        _, errors = proc.communicate(timeout=5)
    finally:
        os.close(master)

    assert proc.returncode != 0
    assert b"[FAIL]" in errors
    assert b"\x1b" not in output + errors
