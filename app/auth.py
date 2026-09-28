import functools
import re
import sqlite3

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import create_user, delete_user_account, get_db


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
            "auth.login", "auth.register", "auth.setup", "static"}:
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    db = get_db()
    user = db.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
    if (user and user["password_hash"]) or current_app.config.get("APP_PASSWORD"):
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
    if first and not first["password_hash"] and not current_app.config.get("APP_PASSWORD"):
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


USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,40}$")


@bp.route("/register", methods=["GET", "POST"])
def register():
    """Public sign-up. New accounts are always plain users, never admins."""
    if not current_app.config.get("ALLOW_REGISTRATION", True):
        abort(404)
    if g.get("user") is not None and not current_app.config.get("AUTH_DISABLED"):
        return redirect(url_for("main.dashboard"))
    db = get_db()
    first = db.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
    if first and not first["password_hash"] and not current_app.config.get("APP_PASSWORD"):
        return redirect(url_for("auth.setup"))
    form = {"username": "", "display_name": ""}
    if request.method == "POST":
        form = {"username": request.form.get("username", "").strip(),
                "display_name": request.form.get("display_name", "").strip()}
        password = request.form.get("password", "")
        if not USERNAME_PATTERN.match(form["username"]):
            flash("Tên đăng nhập gồm 3–40 ký tự: chữ không dấu, số, dấu chấm, gạch dưới "
                  "hoặc gạch ngang.", "error")
        elif len(form["display_name"]) > 80:
            flash("Tên hiển thị tối đa 80 ký tự.", "error")
        elif len(password) < 8:
            flash("Mật khẩu phải có ít nhất 8 ký tự.", "error")
        elif password != request.form.get("confirm_password", ""):
            flash("Mật khẩu nhập lại không khớp.", "error")
        else:
            try:
                user_id = create_user(form["username"], password, "user", form["display_name"])
                db.commit()
            except sqlite3.IntegrityError:
                db.rollback()
                flash("Tên đăng nhập này đã có người dùng. Hãy chọn tên khác.", "error")
            else:
                session.clear()
                session["user_id"] = user_id
                flash("Đã tạo tài khoản. Hãy thêm khóa học từ danh mục chung để bắt đầu "
                      "lập kế hoạch.", "success")
                return redirect(url_for("main.courses"))
    return render_template("register.html", form=form)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/account/password", methods=["GET", "POST"])
def change_password():
    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        if not check_password_hash(g.user["password_hash"],
                                   request.form.get("current_password", "")):
            flash("Mật khẩu hiện tại không đúng.", "error")
        elif len(new_password) < 8:
            flash("Mật khẩu mới phải có ít nhất 8 ký tự.", "error")
        elif new_password != request.form.get("confirm_password", ""):
            flash("Mật khẩu xác nhận không khớp.", "error")
        else:
            get_db().execute("UPDATE users SET password_hash=? WHERE id=?",
                             (generate_password_hash(new_password), g.user["id"]))
            get_db().commit()
            flash("Đã đổi mật khẩu.", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("account_password.html")


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


@bp.post("/admin/users/<int:user_id>/display-name")
@admin_required
def update_display_name(user_id):
    display_name = request.form.get("display_name", "").strip()
    user = get_db().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None:
        abort(404)
    if len(display_name) > 80:
        flash("Tên hiển thị tối đa 80 ký tự.", "error")
    else:
        # An empty name falls back to the username, as when creating an account.
        get_db().execute("UPDATE users SET display_name=? WHERE id=?",
                         (display_name or user["username"], user_id))
        get_db().commit()
        flash(f"Đã đổi tên hiển thị của @{user['username']}.", "success")
    return redirect(url_for("auth.users"))


@bp.post("/admin/users/<int:user_id>/delete")
@admin_required
def delete_user(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None:
        abort(404)
    admins = db.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
    if user_id == g.user["id"]:
        flash("Không thể tự xóa tài khoản đang đăng nhập.", "error")
    elif user["role"] == "admin" and admins <= 1:
        flash("Không thể xóa quản trị viên cuối cùng.", "error")
    elif request.form.get("confirm_username", "").strip() != user["username"]:
        flash("Tên đăng nhập xác nhận chưa khớp. Tài khoản chưa bị xóa.", "error")
    else:
        try:
            backup_id = delete_user_account(user_id, g.user["id"])
            db.commit()
        except sqlite3.Error as exc:
            db.rollback()
            flash(f"Không thể xóa tài khoản: {exc}", "error")
        else:
            flash(f"Đã xóa @{user['username']} và dữ liệu học tập của tài khoản này. "
                  f"Bản sao lưu #{backup_id} được giữ trong cơ sở dữ liệu.", "success")
    return redirect(url_for("auth.users"))


@bp.get("/admin/users/<int:user_id>/progress")
@admin_required
def user_progress(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None:
        abort(404)
    from .reports import courses_with_progress

    courses = courses_with_progress(user_id)
    plans = db.execute("""SELECT p.*,
        (SELECT COUNT(*) FROM plan_courses pc WHERE pc.plan_id=p.id) AS course_count
        FROM study_plans p WHERE p.owner_user_id=? ORDER BY p.priority, p.id""",
        (user_id,)).fetchall()
    return render_template("user_progress.html", viewed_user=user,
                           courses=courses, plans=plans)
