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


def test_validate_uses_explicit_candidate_and_preserves_both_outputs(tmp_path):
    candidate = tmp_path / "candidate.yml"
    with patch("cf_gui.cloudflared.subprocess.run") as run:
        run.return_value.returncode = 1
        run.return_value.stdout = "validation context"
        run.return_value.stderr = "invalid catch-all"
        result = cloudflared.validate_config(candidate)
        assert not result.ok
        assert "validation context" in result.output and "invalid catch-all" in result.output
        assert run.call_args.args[0] == (
            "cloudflared", "tunnel", "--config", str(candidate), "ingress", "validate"
        )
        assert run.call_args.kwargs["check"] is False
        assert "shell" not in run.call_args.kwargs
