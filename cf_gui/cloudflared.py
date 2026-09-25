"""Small wrappers around the installed cloudflared and systemd commands."""

from dataclasses import dataclass
from pathlib import Path
import subprocess


@dataclass
class CommandResult:
    ok: bool
    output: str


def _run(*args: str, timeout: int = 30) -> CommandResult:
    try:
        completed = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=timeout, check=False)
        output = "\n".join(part for part in (completed.stdout.strip(), completed.stderr.strip()) if part)
        return CommandResult(completed.returncode == 0, output or "(brak wyjścia)")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(False, str(exc))


def validate_config(candidate: Path) -> CommandResult:
    return _run("cloudflared", "tunnel", "--config", str(candidate), "ingress", "validate", timeout=30)


def is_active() -> CommandResult:
    return _run("systemctl", "is-active", "cloudflared")


def route_dns(tunnel: str, hostname: str) -> CommandResult:
    return _run("cloudflared", "tunnel", "route", "dns", tunnel, hostname, timeout=60)


def service_status() -> CommandResult:
    return _run("systemctl", "status", "cloudflared", "--no-pager", "--lines=0")


def recent_logs() -> CommandResult:
    return _run("journalctl", "-u", "cloudflared", "-n", "20", "--no-pager", "-o", "short")


def restart_service() -> tuple[CommandResult, CommandResult, CommandResult]:
    restart = _run("systemctl", "restart", "cloudflared", timeout=60)
    return restart, service_status(), recent_logs()
