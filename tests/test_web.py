"""Exercise login and revision-aware mutations through Flask's HTTP client."""

import re
from unittest.mock import patch

import pytest
from werkzeug.security import generate_password_hash

from cf_gui.cloudflared import CommandResult
from cf_gui.config import ConfigStore
from cf_gui.web import create_app


SOURCE = """tunnel: test-tunnel
ingress:
  - hostname: old.example.com
    service: http://localhost:3000
  - service: http_status:404
"""


def field(response, name: str) -> str:
    pattern = rb'name="' + name.encode() + rb'" value="([^"]+)"'
    match = re.search(pattern, response.data)
    assert match is not None
    return match.group(1).decode()


def result(ok=True, output="OK"):
    return CommandResult(ok, output)


def service_results(ok=True):
    return result(ok, "restarted"), result(ok, "active status"), result(True, "last log")


def client_for(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(SOURCE)
    app = create_app(config_path=str(path), password_hash=generate_password_hash("secret"), secret_key="test-key")
    app.testing = True
    client = app.test_client()
    assert client.get("/").status_code == 302
    login = client.get("/login")
    response = client.post("/login", data={"password": "secret", "csrf_token": field(login, "csrf_token")},
                           follow_redirects=True)
    assert response.status_code == 200
    return path, client


def test_login_add_dns_and_restart(tmp_path):
    path, client = client_for(tmp_path)
    form = client.get("/ingress/new")
    with patch("cf_gui.cloudflared.validate_config", return_value=result()) as validate, \
         patch("cf_gui.cloudflared.restart_service", side_effect=[service_results(), service_results()]) as restart, \
         patch("cf_gui.cloudflared.is_active", return_value=result(True, "active")), \
         patch("cf_gui.web.cloudflared.route_dns", return_value=result(True, "created")) as route:
        response = client.post("/ingress", data={
            "csrf_token": field(form, "csrf_token"), "revision": field(form, "revision"),
            "hostname": "new.example.com", "service": "http://localhost:8080", "create_dns": "1"
        }, follow_redirects=True)
        assert response.status_code == 200
        assert b"new.example.com" in response.data
        assert b"Aktywacja zako" in response.data
        route.assert_called_once_with("test-tunnel", "new.example.com")
        validate.assert_called_once()
        assert not validate.call_args.args[0].exists()
        assert restart.call_count == 1

        response = client.post("/service/restart", data={"csrf_token": field(response, "csrf_token")})
        assert response.status_code == 200
        assert b"restarted" in response.data and b"last log" in response.data
    assert ConfigStore(path).entries(ConfigStore(path).load())[1][1] == "new.example.com"
    assert len(list(tmp_path.glob("config.yml.bak.*"))) == 1


@pytest.mark.parametrize("operation", ["edit", "delete"])
def test_stale_index_cannot_change_a_different_entry(tmp_path, operation):
    path, client = client_for(tmp_path)
    shown = client.get("/ingress/0/edit" if operation == "edit" else "/")
    # Another writer inserts an entry before the one the user saw at index 0.
    external = SOURCE.replace("  - hostname: old.example.com", "  - hostname: external.example.com\n    service: http://localhost:9090\n  - hostname: old.example.com")
    path.write_text(external)
    url = "/ingress/0" if operation == "edit" else "/ingress/0/delete"
    data = {"csrf_token": field(shown, "csrf_token"), "revision": field(shown, "revision")}
    if operation == "edit":
        data.update(hostname="edited.example.com", service="http://localhost:4000")
    with patch("cf_gui.cloudflared.validate_config") as validate:
        response = client.post(url, data=data)
    assert response.status_code == 409
    assert "Konfiguracja zmieniła się".encode() in response.data
    assert path.read_text() == external
    validate.assert_not_called()


def test_invalid_cloudflared_config_is_shown_without_activation(tmp_path):
    path, client = client_for(tmp_path)
    form = client.get("/ingress/new")
    with patch("cf_gui.cloudflared.validate_config", return_value=result(False, "stdout info\nstderr invalid")), \
         patch("cf_gui.cloudflared.restart_service") as restart:
        response = client.post("/ingress", data={
            "csrf_token": field(form, "csrf_token"), "revision": field(form, "revision"),
            "hostname": "new.example.com", "service": "bad-service"
        })
    assert response.status_code == 400
    assert b"stdout info" in response.data and b"stderr invalid" in response.data
    assert path.read_text() == SOURCE
    restart.assert_not_called()


def test_rollback_status_is_distinct_in_gui(tmp_path):
    path, client = client_for(tmp_path)
    form = client.get("/ingress/0/edit")
    with patch("cf_gui.cloudflared.validate_config", return_value=result()), \
         patch("cf_gui.cloudflared.restart_service", side_effect=[service_results(False), service_results(True)]), \
         patch("cf_gui.cloudflared.is_active", side_effect=[result(False, "failed"), result(True, "active")]):
        response = client.post("/ingress/0", data={
            "csrf_token": field(form, "csrf_token"), "revision": field(form, "revision"),
            "hostname": "changed.example.com", "service": "http://localhost:4000"
        }, follow_redirects=True)
    assert response.status_code == 200
    assert "rollback zakończony sukcesem".encode() in response.data
    assert path.read_text() == SOURCE
