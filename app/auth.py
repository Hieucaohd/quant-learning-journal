import functools
import sqlite3

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import create_user, get_db


bp = Blueprint("auth", __name__)


def current_user_id():
    if g.get("user") is None:
        if current_app.config.get("AUTH_DISABLED"):
            user = get_db().execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
            if user:
                return user["id"]
        abort(401)
    return g.user["id"]


def admin_required(view):
    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.user["role"] != "admin":
            abort(403)
        return view(**kwargs)
    return wrapped


@bp.before_app_request
def load_logged_in_user():
    if g.get("user") is not None:
        return
    if g.get("_auth_loaded"):
        return
    g._auth_loaded = True
    if current_app.config.get("AUTH_DISABLED"):
        g.user = get_db().execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
        return
    user_id = session.get("user_id")
    if user_id is not None:
        g.user = get_db().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    else:
        g.user = None


@bp.before_app_request
def require_login():
    if g.get("user") is None and request.endpoint not in {
            "auth.login", "auth.setup", "static"}:
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    db = get_db()
    user = db.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
    if user and user["password_hash"]:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or len(password) < 8:
            flash("Tên đăng nhập là bắt buộc và mật khẩu phải có ít nhất 8 ký tự.", "error")
        else:
            db.execute("""UPDATE users SET username=?, display_name=?, password_hash=?, role='admin'
                WHERE id=?""", (username, username, generate_password_hash(password), user["id"]))
            db.commit()
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("main.dashboard"))
    return render_template("setup.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    first = get_db().execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
    if first and not first["password_hash"]:
        return redirect(url_for("auth.setup"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        user = get_db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user is None or not check_password_hash(user["password_hash"],
                                                   request.form.get("password", "")):
            flash("Tên đăng nhập hoặc mật khẩu không đúng.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            target = request.args.get("next", "")
            return redirect(target if target.startswith("/") and not target.startswith("//")
                            else url_for("main.dashboard"))
    return render_template("login.html")


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/admin/users", methods=["GET", "POST"])
@admin_required
def users():
    db = get_db()
    if request.method == "POST":
        try:
            create_user(request.form.get("username", ""), request.form.get("password", ""),
                        request.form.get("role", "user"),
                        request.form.get("display_name", ""))
            db.commit()
            flash("Đã tạo tài khoản.", "success")
            return redirect(url_for("auth.users"))
        except (ValueError, sqlite3.IntegrityError) as exc:
            db.rollback()
            flash(str(exc) if isinstance(exc, ValueError)
                  else "Tên đăng nhập đã tồn tại.", "error")
    rows = db.execute("""SELECT u.*,
        (SELECT COUNT(*) FROM courses c WHERE c.owner_user_id=u.id) AS course_count,
        (SELECT COUNT(*) FROM study_plans p WHERE p.owner_user_id=u.id) AS plan_count
        FROM users u ORDER BY u.username""").fetchall()
    return render_template("users.html", users=rows)


@bp.post("/admin/users/<int:user_id>/password")
@admin_required
def reset_password(user_id):
    password = request.form.get("password", "")
    if len(password) < 8:
        flash("Mật khẩu phải có ít nhất 8 ký tự.", "error")
    elif not get_db().execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
        abort(404)
    else:
        get_db().execute("UPDATE users SET password_hash=? WHERE id=?",
                         (generate_password_hash(password), user_id))
        get_db().commit()
        flash("Đã đặt lại mật khẩu.", "success")
    return redirect(url_for("auth.users"))


@bp.get("/admin/users/<int:user_id>/progress")
@admin_required
def user_progress(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None:
        abort(404)
    courses = db.execute("""SELECT c.* FROM courses c
        WHERE c.owner_user_id=? ORDER BY c.id""", (user_id,)).fetchall()
    plans = db.execute("""SELECT p.*,
        (SELECT COUNT(*) FROM plan_courses pc WHERE pc.plan_id=p.id) AS course_count
        FROM study_plans p WHERE p.owner_user_id=? ORDER BY p.priority, p.id""",
        (user_id,)).fetchall()
    return render_template("user_progress.html", viewed_user=user,
                           courses=courses, plans=plans)
