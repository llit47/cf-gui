"""Single administrator login and basic form protection."""

from functools import wraps
import hmac
import secrets

from flask import abort, redirect, request, session, url_for
from werkzeug.security import check_password_hash


def password_matches(password: str, password_hash: str) -> bool:
    return check_password_hash(password_hash, password)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def require_csrf() -> None:
    provided = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    if not expected or not hmac.compare_digest(provided, expected):
        abort(400, "Nieprawidłowy token formularza.")
