"""HTTP routes joining forms, config storage and cloudflared commands."""

from __future__ import annotations

import os

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

from . import cloudflared
from .activation import Activator, ActivationResult, ValidationError
from .auth import csrf_token, login_required, password_matches, require_csrf
from .config import ConfigError, ConfigStore, StaleConfigError


def create_app(*, config_path: str | None = None, password_hash: str | None = None,
               secret_key: str | None = None) -> Flask:
    password_hash = password_hash or os.environ.get("CF_GUI_PASSWORD_HASH")
    secret_key = secret_key or os.environ.get("CF_GUI_SECRET_KEY")
    if not password_hash or not secret_key:
        raise RuntimeError("CF_GUI_PASSWORD_HASH i CF_GUI_SECRET_KEY są wymagane.")

    app = Flask(__name__)
    app.secret_key = secret_key
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
    store = ConfigStore(config_path or os.environ.get("CF_GUI_CONFIG_PATH", "/etc/cloudflared/config.yml"))
    activator = Activator(store)
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def check_form_token():
        if request.method == "POST":
            require_csrf()

    @app.errorhandler(StaleConfigError)
    def stale_config(error):
        return render_template("error.html", title="Konfiguracja zmieniła się", message=str(error)), 409

    @app.errorhandler(ConfigError)
    def config_error(error):
        return render_template("error.html", title="Błąd konfiguracji", message=str(error)), 500

    def activation_response(result: ActivationResult):
        if result.state == "succeeded":
            flash(f"Aktywacja zakończona sukcesem. Backup: {result.backup}.", "success")
            return None
        status = 409 if result.state == "rolled_back" else 500
        return render_template("activation_result.html", result=result), status

    def config_failure(error: ConfigError, *, form: str | None = None, **context):
        if isinstance(error, ValidationError):
            return render_template(
                "error.html", title="Candidate nie przeszedł walidacji cloudflared",
                message="Aktywny config.yml nie został zmieniony. Cloudflared nie był restartowany.",
                details=error.output,
            ), 400
        if form:
            return render_template(form, error=str(error), **context), 400
        return render_template("error.html", title="Błąd konfiguracji", message=str(error)), 400

    @app.get("/login")
    def login():
        if session.get("admin"):
            return redirect(url_for("index"))
        return render_template("login.html")

    @app.post("/login")
    def login_post():
        if password_matches(request.form.get("password", ""), password_hash):
            session.clear()
            session["admin"] = True
            return redirect(url_for("index"))
        flash("Nieprawidłowe hasło.", "error")
        return render_template("login.html"), 401

    @app.post("/logout")
    @login_required
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def index():
        snapshot = store.load_snapshot()
        return render_template("index.html", entries=store.entries(snapshot.document), revision=snapshot.revision,
                               tunnel=snapshot.document.get("tunnel", "(brak w config.yml)"))

    @app.get("/ingress/new")
    @login_required
    def new_entry():
        snapshot = store.load_snapshot()
        return render_template("entry_form.html", title="Dodaj wpis", action=url_for("add_entry"),
                               hostname="", service="", is_new=True, revision=snapshot.revision)

    @app.post("/ingress")
    @login_required
    def add_entry():
        hostname = request.form.get("hostname", "").strip()
        revision = request.form.get("revision", "")
        try:
            result = activator.apply(revision, lambda document: store.add(document, hostname, request.form.get("service", "")))
        except StaleConfigError:
            raise
        except ConfigError as exc:
            return config_failure(exc, form="entry_form.html", title="Dodaj wpis", action=url_for("add_entry"),
                                  hostname=hostname, service=request.form.get("service", ""),
                                  is_new=True, revision=revision)
        failure_response = activation_response(result)
        if failure_response is not None:
            return failure_response
        if request.form.get("create_dns"):
            dns_response = _route_dns(store.load(), hostname, after_activation=True)
            if dns_response is not None:
                return dns_response
        return redirect(url_for("index"))

    @app.get("/ingress/<int:index>/edit")
    @login_required
    def edit_entry(index: int):
        snapshot = store.load_snapshot()
        try:
            item = store._entry(snapshot.document, index)
        except ConfigError:
            abort(404)
        return render_template("entry_form.html", title="Edytuj wpis", action=url_for("update_entry", index=index),
                               hostname=item["hostname"], service=item["service"], is_new=False,
                               revision=snapshot.revision)

    @app.post("/ingress/<int:index>")
    @login_required
    def update_entry(index: int):
        hostname = request.form.get("hostname", "")
        service = request.form.get("service", "")
        revision = request.form.get("revision", "")
        try:
            result = activator.apply(revision, lambda document: store.edit(document, index, hostname, service))
        except StaleConfigError:
            raise
        except ConfigError as exc:
            return config_failure(exc, form="entry_form.html", title="Edytuj wpis",
                                  action=url_for("update_entry", index=index), hostname=hostname,
                                  service=service, is_new=False, revision=revision)
        failure_response = activation_response(result)
        if failure_response is not None:
            return failure_response
        return redirect(url_for("index"))

    @app.post("/ingress/<int:index>/delete")
    @login_required
    def delete_entry(index: int):
        try:
            result = activator.apply(request.form.get("revision", ""), lambda document: store.delete(document, index))
        except StaleConfigError:
            raise
        except ConfigError as exc:
            return config_failure(exc)
        failure_response = activation_response(result)
        if failure_response is not None:
            return failure_response
        return redirect(url_for("index"))

    def _route_dns(document: dict, hostname: str, *, after_activation: bool = False):
        tunnel = document.get("tunnel")
        if not isinstance(tunnel, str) or not tunnel.strip():
            flash("Brak nazwy/ID tunelu w config.yml; nie utworzono DNS.", "error")
            return
        result = cloudflared.route_dns(tunnel, hostname)
        if result.ok:
            flash("Rekord DNS został utworzony.", "success")
            return
        message = ("Zmiana configu została aktywowana, ale utworzenie DNS nie powiodło się."
                   if after_activation else "Utworzenie DNS nie powiodło się. Config nie został zmieniony.")
        return render_template("error.html", title="Błąd tworzenia DNS", message=message,
                               details=result.output), 502

    @app.post("/ingress/<int:index>/route-dns")
    @login_required
    def route_entry_dns(index: int):
        with store.write_lock():
            snapshot = store.load_snapshot()
            store.assert_revision(request.form.get("revision", ""))
            try:
                hostname = store._entry(snapshot.document, index)["hostname"]
            except ConfigError:
                abort(404)
            dns_response = _route_dns(snapshot.document, hostname)
            if dns_response is not None:
                return dns_response
        return redirect(url_for("index"))

    @app.get("/service")
    @login_required
    def service():
        return render_template("service.html", restart=None, status=cloudflared.service_status(),
                               logs=cloudflared.recent_logs())

    @app.post("/service/restart")
    @login_required
    def restart():
        with store.write_lock():
            restart_result, status, logs = cloudflared.restart_service()
        return render_template("service.html", restart=restart_result, status=status, logs=logs)

    return app
