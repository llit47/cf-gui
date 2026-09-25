from unittest.mock import patch

from cf_gui import cloudflared


def test_route_dns_uses_argument_list():
    with patch("cf_gui.cloudflared.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "created"
        run.return_value.stderr = ""
        assert cloudflared.route_dns("my-tunnel", "new.example.com").ok
        assert run.call_args.args[0] == (
            "cloudflared", "tunnel", "route", "dns", "my-tunnel", "new.example.com"
        )


def test_restart_always_collects_status_and_logs():
    with patch("cf_gui.cloudflared._run") as run:
        run.side_effect = [cloudflared.CommandResult(False, "failed"),
                           cloudflared.CommandResult(False, "inactive"),
                           cloudflared.CommandResult(True, "log line")]
        restart, status, logs = cloudflared.restart_service()
        assert not restart.ok and status.output == "inactive" and logs.output == "log line"
        assert run.call_count == 3
