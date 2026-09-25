"""Exercise the admin flow through Flask's HTTP test client."""

import re
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from cf_gui.config import ConfigStore
from cf_gui.web import create_app


SOURCE = """tunnel: test-tunnel
ingress:
  - hostname: old.example.com
    service: http://localhost:3000
  - service: http_status:404
"""


def token(response) -> str:
    match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
    assert match is not None
    return match.group(1).decode()


def test_login_add_dns_and_restart(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(SOURCE)
    app = create_app(config_path=str(path), password_hash=generate_password_hash("secret"), secret_key="test-key")
    app.testing = True
    client = app.test_client()

    assert client.get("/").status_code == 302
    login = client.get("/login")
    assert login.status_code == 200
    response = client.post("/login", data={"password": "secret", "csrf_token": token(login)}, follow_redirects=True)
    assert response.status_code == 200
    assert b"old.example.com" in response.data

    form = client.get("/ingress/new")
    with patch("cf_gui.web.cloudflared.route_dns") as route:
        route.return_value.ok = True
        route.return_value.output = "created"
        response = client.post("/ingress", data={
            "csrf_token": token(form), "hostname": "new.example.com",
            "service": "http://localhost:8080", "create_dns": "1"
        }, follow_redirects=True)
        route.assert_called_once_with("test-tunnel", "new.example.com")
    assert response.status_code == 200
    assert b"new.example.com" in response.data
    assert ConfigStore(path).entries(ConfigStore(path).load())[1][1] == "new.example.com"
    assert len(list(tmp_path.glob("config.yml.bak.*"))) == 1

    with patch("cf_gui.web.cloudflared.restart_service") as restart:
        from cf_gui.cloudflared import CommandResult
        restart.return_value = (CommandResult(True, "restarted"), CommandResult(True, "active"), CommandResult(True, "last log"))
        response = client.post("/service/restart", data={"csrf_token": token(response)})
    assert response.status_code == 200
    assert b"restarted" in response.data and b"last log" in response.data
