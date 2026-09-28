import os
import sys
from pathlib import Path

from flask import Flask
from dotenv import load_dotenv


# The production database. These are the only Turso variables the app reads;
# any other TURSO_* variables (e.g. an unprefixed TURSO_DATABASE_URL) are ignored.
TURSO_URL_ENV = "quant_learning_journal_TURSO_DATABASE_URL"
TURSO_TOKEN_ENV = "quant_learning_journal_TURSO_AUTH_TOKEN"


def turso_credentials_from_env():
    return os.environ.get(TURSO_URL_ENV, ""), os.environ.get(TURSO_TOKEN_ENV, "")


def is_remote_database(url):
    return (url or "").lower().startswith(("libsql://", "http://", "https://", "ws://", "wss://"))


def create_app(test_config=None):
    app = Flask(__name__)
    source_root = Path(__file__).resolve().parent.parent
    load_dotenv(source_root / ".env", override=False)
    on_vercel = bool(os.environ.get("VERCEL"))
    turso_url, turso_token = turso_credentials_from_env()
    data_root = (Path(sys.executable).resolve().parent
                 if getattr(sys, "frozen", False) else source_root)
    app.config.update(
        DATABASE=str(data_root / "data" / "journal.sqlite3"),
        EXPORT_DIR=str(Path("/tmp/exports") if on_vercel else data_root / "exports"),
        BACKUP_DIR=str(Path("/tmp/backups") if on_vercel else data_root / "database" / "backups"),
        TURSO_DATABASE_URL=turso_url,
        TURSO_AUTH_TOKEN=turso_token,
        APP_USERNAME=os.environ.get("APP_USERNAME", "quant"),
        # Used until the browser reports its own zone (see app/timezone.py).
        DEFAULT_TIMEZONE=os.environ.get("DEFAULT_TIMEZONE", "Asia/Ho_Chi_Minh"),
        APP_PASSWORD=os.environ.get("APP_PASSWORD", ""),
        # Public sign-up for new "user" accounts; set ALLOW_REGISTRATION=0 to turn it off.
        ALLOW_REGISTRATION=os.environ.get("ALLOW_REGISTRATION", "1") != "0",
        SECRET_KEY=os.environ.get("JOURNAL_SECRET_KEY", "local-only-journal-key"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=on_vercel,
        AUTH_DISABLED=False,
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
        if app.config.get("TESTING") and "AUTH_DISABLED" not in test_config:
            app.config["AUTH_DISABLED"] = True
        # Tests that explicitly select a local SQLite file must not inherit the
        # developer's real Turso credentials from .env. Integration tests can
        # still opt in by passing TURSO_DATABASE_URL in test_config.
        if "DATABASE" in test_config and "TURSO_DATABASE_URL" not in test_config:
            app.config["TURSO_DATABASE_URL"] = ""
            app.config["TURSO_AUTH_TOKEN"] = ""
    if "AUTO_MIGRATE_DATABASE" not in (test_config or {}):
        # Only local SQLite files migrate on startup. A remote (real) database is
        # migrated only when someone deliberately sets AUTO_MIGRATE_DATABASE=1.
        app.config["AUTO_MIGRATE_DATABASE"] = (
            not is_remote_database(app.config["TURSO_DATABASE_URL"])
            or os.environ.get("AUTO_MIGRATE_DATABASE") == "1")
    if on_vercel and not app.config["TURSO_DATABASE_URL"]:
        raise RuntimeError("Không tìm thấy cặp biến Turso trên Vercel; SQLite local không lưu bền vững.")
    if on_vercel and not app.config["TURSO_AUTH_TOKEN"]:
        raise RuntimeError("Thiếu token xác thực cho Turso trên Vercel.")
    if on_vercel and not app.config["APP_PASSWORD"]:
        raise RuntimeError("Thiếu APP_PASSWORD trên Vercel; ứng dụng không được triển khai công khai.")

    from . import db, timezone

    timezone.init_app(app)
    db.init_app(app)
    from .auth import bp as auth_bp
    from .routes import bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(bp)

    @app.errorhandler(404)
    def not_found(_error):
        return "Không tìm thấy trang hoặc mục dữ liệu được yêu cầu.", 404

    @app.errorhandler(400)
    def bad_request(_error):
        return "Yêu cầu không hợp lệ.", 400

    @app.errorhandler(401)
    def unauthorized(_error):
        return "Bạn cần đăng nhập để tiếp tục.", 401

    @app.errorhandler(403)
    def forbidden(_error):
        return "Bạn không có quyền xem hoặc thay đổi nội dung này.", 403

    @app.errorhandler(413)
    def too_large(_error):
        return "File JSON quá lớn (giới hạn 2 MB).", 413

    return app
