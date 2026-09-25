"""Exercise login and revision-aware mutations through Flask's HTTP client."""

from html.parser import HTMLParser
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


def test_only_success_and_info_notices_auto_dismiss(tmp_path):
    _, client = client_for(tmp_path)
    with client.session_transaction() as session:
        session["_flashes"] = [("success", "saved"), ("info", "ready"),
                               ("warning", "check"), ("error", "failed")]

    class Notices(HTMLParser):
        def __init__(self):
            super().__init__()
            self.items = {}

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            classes = attributes.get("class", "").split()
            if tag == "div" and "notice" in classes:
                self.items[classes[1]] = attributes

    response = client.get("/")
    notices = Notices()
    notices.feed(response.get_data(as_text=True))
    assert response.status_code == 200
    for category in ("success", "info"):
        assert "data-auto-dismiss" in notices.items[category]
        assert notices.items[category]["role"] == "status"
    for category in ("warning", "error"):
        assert "data-auto-dismiss" not in notices.items[category]
        assert notices.items[category]["role"] == "alert"


def test_login_add_dns_and_restart(tmp_path):
    path, client = client_for(tmp_path)
    form = client.get("/ingress/new")
    with patch("cf_gui.cloudflared.validate_config", return_value=result()) as validate, \
         patch("cf_gui.cloudflared.restart_service", side_effect=[service_results(), service_results()]) as restart, \
         patch("cf_gui.cloudflared.is_active", return_value=result(True, "active")), \
         patch("cf_gui.web.cloudflared.route_dns", return_value=result(True, "created")) as route:
        response = client.post("/ingress", data={
            "csrf_token": field(form, "csrf_token"), "revision": field(form, "revision"),
            "hostname": "new.example.com", "service": "192.0.2.79:8080", "create_dns": "1"
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
    assert ConfigStore(path).entries(ConfigStore(path).load())[1] == (
        1, "new.example.com", "http://192.0.2.79:8080"
    )
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
            "hostname": "new.example.com", "service": "http://localhost:3001"
        })
    assert response.status_code == 400
    assert b"stdout info" in response.data and b"stderr invalid" in response.data
    assert path.read_text() == SOURCE
    restart.assert_not_called()


@pytest.mark.parametrize(("hostname", "service"), [
    ("dupadupa", "origin:8096"),
    ("foo.*.example.com", "origin:8096"),
    ("game.example.com", "192.0.2.333:8000"),
    ("game.example.com", "origin:8096/path"),
    ("game.example.com", "http_status:099"),
])
def test_invalid_form_is_rejected_before_candidate_and_cloudflared(tmp_path, hostname, service):
    path, client = client_for(tmp_path)
    form = client.get("/ingress/new")
    with patch("cf_gui.config.ConfigStore.candidate") as candidate, \
         patch("cf_gui.cloudflared.validate_config") as validate, \
         patch("cf_gui.cloudflared.restart_service") as restart:
        response = client.post("/ingress", data={
            "csrf_token": field(form, "csrf_token"), "revision": field(form, "revision"),
            "hostname": hostname, "service": service,
        })
    assert response.status_code == 400
    assert path.read_text() == SOURCE
    candidate.assert_not_called()
    validate.assert_not_called()
    restart.assert_not_called()


@pytest.mark.parametrize("operation", ["add", "edit", "delete"])
def test_long_validation_output_stays_in_body_and_out_of_session(tmp_path, operation):
    path, client = client_for(tmp_path)
    shown = client.get("/ingress/new" if operation == "add" else
                       "/ingress/0/edit" if operation == "edit" else "/")
    url = "/ingress" if operation == "add" else "/ingress/0" if operation == "edit" else "/ingress/0/delete"
    data = {"csrf_token": field(shown, "csrf_token"), "revision": field(shown, "revision")}
    if operation != "delete":
        data.update(hostname="new.example.com", service="http://localhost:4000")
    diagnostics = "VALIDATION-BEGIN:" + "".join(f"{value:05d}:invalid;" for value in range(1500)) + ":VALIDATION-END"
    with patch("cf_gui.cloudflared.validate_config", return_value=result(False, diagnostics)), \
         patch("cf_gui.cloudflared.restart_service") as restart, \
         patch("cf_gui.web.flash") as flash:
        response = client.post(url, data=data)
    assert response.status_code == 400
    assert diagnostics.encode() in response.data
    assert "Candidate nie przeszedł walidacji".encode() in response.data
    assert b"Aktywny config.yml nie zosta" in response.data
    assert b"Cloudflared nie by" in response.data
    assert path.read_text() == SOURCE
    assert not list(tmp_path.glob("config.yml.bak.*"))
    restart.assert_not_called()
    flash.assert_not_called()
    with client.session_transaction() as session:
        assert "_flashes" not in session
        assert "VALIDATION-BEGIN" not in str(dict(session))
    assert all(len(header) < 4096 for header in response.headers.getlist("Set-Cookie"))


def test_long_dns_error_stays_in_body_and_out_of_session(tmp_path):
    path, client = client_for(tmp_path)
    shown = client.get("/")
    diagnostics = "DNS-BEGIN:" + "".join(f"{value:05d}:route-error;" for value in range(1500)) + ":DNS-END"
    with patch("cf_gui.web.cloudflared.route_dns", return_value=result(False, diagnostics)) as route, \
         patch("cf_gui.web.flash") as flash:
        response = client.post("/ingress/0/route-dns", data={
            "csrf_token": field(shown, "csrf_token"), "revision": field(shown, "revision")
        })
    assert response.status_code == 502
    assert diagnostics.encode() in response.data
    assert b"Utworzenie DNS nie powiod" in response.data
    assert path.read_text() == SOURCE
    route.assert_called_once_with("test-tunnel", "old.example.com")
    flash.assert_not_called()
    with client.session_transaction() as session:
        assert "_flashes" not in session
        assert "DNS-BEGIN" not in str(dict(session))
    assert all(len(header) < 4096 for header in response.headers.getlist("Set-Cookie"))


def test_successful_dns_flashes_only_controlled_message(tmp_path):
    _, client = client_for(tmp_path)
    shown = client.get("/")
    output = "DNS-CLI-OUTPUT:" + "".join(f"{value:05d}:ok;" for value in range(1500))
    with patch("cf_gui.web.cloudflared.route_dns", return_value=result(True, output)):
        response = client.post("/ingress/0/route-dns", data={
            "csrf_token": field(shown, "csrf_token"), "revision": field(shown, "revision")
        })
    assert response.status_code == 302
    with client.session_transaction() as session:
        assert session["_flashes"] == [("success", "Rekord DNS został utworzony.")]
        assert "DNS-CLI-OUTPUT" not in str(dict(session))


def test_dns_failure_after_add_shows_diagnostics_without_cookie_output(tmp_path):
    path, client = client_for(tmp_path)
    form = client.get("/ingress/new")
    diagnostics = "DNS-AFTER-ADD:" + "".join(f"{value:05d}:failed;" for value in range(1500))
    with patch("cf_gui.cloudflared.validate_config", return_value=result()), \
         patch("cf_gui.cloudflared.restart_service", return_value=service_results()), \
         patch("cf_gui.cloudflared.is_active", return_value=result(True, "active")), \
         patch("cf_gui.web.cloudflared.route_dns", return_value=result(False, diagnostics)):
        response = client.post("/ingress", data={
            "csrf_token": field(form, "csrf_token"), "revision": field(form, "revision"),
            "hostname": "new.example.com", "service": "http://localhost:4000", "create_dns": "1"
        })
    assert response.status_code == 502
    assert diagnostics.encode() in response.data
    assert b"Zmiana configu zosta" in response.data
    assert "new.example.com" in path.read_text()
    with client.session_transaction() as session:
        assert "DNS-AFTER-ADD" not in str(dict(session))
        assert "_flashes" not in session


def test_rolled_back_response_contains_full_diagnostics(tmp_path):
    path, client = client_for(tmp_path)
    form = client.get("/ingress/0/edit")
    first = (result(False, "first restart output"), result(False, "first status output"),
             result(True, "first journal output"))
    second = (result(True, "second restart output"), result(True, "second status output"),
              result(True, "second journal output"))
    with patch("cf_gui.cloudflared.validate_config", return_value=result()), \
         patch("cf_gui.cloudflared.restart_service", side_effect=[first, second]), \
         patch("cf_gui.cloudflared.is_active", side_effect=[result(False, "first is-active output"),
                                                            result(True, "second is-active output")]), \
         patch("cf_gui.web.flash") as flash:
        response = client.post("/ingress/0", data={
            "csrf_token": field(form, "csrf_token"), "revision": field(form, "revision"),
            "hostname": "changed.example.com", "service": "http://localhost:4000"
        })
    assert response.status_code == 409
    flash.assert_not_called()
    assert "Żądana zmiana nie została aktywowana".encode() in response.data
    for marker in ("first restart output", "first status output", "first is-active output",
                   "first journal output", "Poprzedni config został przywrócony",
                   "second restart output", "second status output", "second is-active output",
                   "second journal output"):
        assert marker.encode() in response.data
    assert path.read_text() == SOURCE


def test_rollback_failed_response_contains_full_diagnostics(tmp_path):
    path, client = client_for(tmp_path)
    form = client.get("/ingress/0/edit")
    first = (result(False, "first restart failed"), result(False, "first status failed"),
             result(True, "first journal failure"))
    second = (result(False, "second restart failed"), result(False, "second status failed"),
              result(True, "second journal failure"))
    with patch("cf_gui.cloudflared.validate_config", return_value=result()), \
         patch("cf_gui.cloudflared.restart_service", side_effect=[first, second]), \
         patch("cf_gui.cloudflared.is_active", side_effect=[result(False, "first inactive"),
                                                            result(False, "second inactive")]):
        response = client.post("/ingress/0", data={
            "csrf_token": field(form, "csrf_token"), "revision": field(form, "revision"),
            "hostname": "changed.example.com", "service": "http://localhost:4000"
        })
    assert response.status_code == 500
    assert "Aktywacja i rollback nieudane".encode() in response.data
    for marker in ("first restart failed", "first status failed", "first inactive",
                   "first journal failure", "Poprzedni config został przywrócony",
                   "second restart failed", "second status failed", "second inactive",
                   "second journal failure"):
        assert marker.encode() in response.data
    assert path.read_text() == SOURCE


def test_long_diagnostics_are_in_body_not_session_cookie(tmp_path):
    from cf_gui.config import ConfigError

    path, client = client_for(tmp_path)
    form = client.get("/ingress/0/edit")
    long_journal = "JOURNAL-BEGIN:" + "".join(f"{value:05d}:diagnostic;" for value in range(1500)) + ":JOURNAL-END"
    first = (result(False, "restart failed"), result(False, "status failed"), result(True, long_journal))
    with patch("cf_gui.cloudflared.validate_config", return_value=result()), \
         patch("cf_gui.cloudflared.restart_service", return_value=first), \
         patch("cf_gui.cloudflared.is_active", return_value=result(False, "inactive")), \
         patch("cf_gui.config.ConfigStore.restore", side_effect=ConfigError("restore failed")), \
         patch("cf_gui.web.flash") as flash:
        response = client.post("/ingress/0", data={
            "csrf_token": field(form, "csrf_token"), "revision": field(form, "revision"),
            "hostname": "changed.example.com", "service": "http://localhost:4000"
        })
    assert response.status_code == 500
    flash.assert_not_called()
    assert long_journal.encode() in response.data
    assert b"restore failed" in response.data
    with client.session_transaction() as session:
        assert "_flashes" not in session
        assert "JOURNAL-BEGIN" not in str(dict(session))
    assert all(len(header) < 4096 for header in response.headers.getlist("Set-Cookie"))
    assert "changed.example.com" in path.read_text()  # restore failed; candidate remains
