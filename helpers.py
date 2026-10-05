"""Shared helpers: database connection, login/admin guards and CSRF protection."""
import os
import secrets
import sqlite3
from functools import wraps

from flask import abort, current_app, g, redirect, request, session, url_for
from markupsafe import Markup


# ---------- database ----------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(os.path.join(current_app.root_path, "learniq.db"))
        g.db.row_factory = sqlite3.Row
        upgrade_schema(g.db)
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def upgrade_schema(db):
    """Add newer tables/columns to an existing database, so old data keeps working."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS explanations ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, question_id INTEGER NOT NULL, "
        "selected TEXT NOT NULL, text TEXT NOT NULL, UNIQUE (question_id, selected))"
    )
    for table, column, definition in (("users", "is_admin", "INTEGER NOT NULL DEFAULT 0"),
                                      ("questions", "active", "INTEGER NOT NULL DEFAULT 1")):
        existing = [row[1] for row in db.execute(f"PRAGMA table_info({table})")]
        if existing and column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    db.commit()


# ---------- login and admin guards ----------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = None
        if "user_id" in session:
            user = get_db().execute("SELECT is_admin FROM users WHERE id = ?",
                                    (session["user_id"],)).fetchone()
        if user is None:  # not logged in, or the account was deleted
            session.clear()
            return redirect(url_for("login"))
        g.is_admin = bool(user["is_admin"])
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.get("is_admin"):
            abort(403)
        return view(*args, **kwargs)
    return login_required(wrapped)


# ---------- CSRF protection (stops other websites from submitting our forms) ----------
def csrf_field():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return Markup(f'<input type="hidden" name="csrf" value="{session["csrf"]}">')


def check_csrf():
    if request.method == "POST":
        sent = request.form.get("csrf", "")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            abort(400, "This form expired. Go back, refresh the page and try again.")
