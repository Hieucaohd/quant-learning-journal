import os
import sys
from pathlib import Path

from flask import Flask
from dotenv import load_dotenv


def turso_credentials_from_env():
    direct = (os.environ.get("TURSO_DATABASE_URL", ""),
              os.environ.get("TURSO_AUTH_TOKEN", ""))
    if all(direct):
        return direct

    url_suffix = "TURSO_DATABASE_URL"
    candidates = []
    for name, url in os.environ.items():
        if not name.endswith(url_suffix) or name == url_suffix or not url:
            continue
        prefix = name[:-len(url_suffix)]
        token = os.environ.get(f"{prefix}TURSO_AUTH_TOKEN", "")
        if token:
            candidates.append((url, token))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise RuntimeError(
            "Có nhiều cặp biến Turso có tiền tố. Hãy đặt TURSO_DATABASE_URL và "
            "TURSO_AUTH_TOKEN để chọn database cần dùng.")
    return direct


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
        AUTO_MIGRATE_DATABASE=True,
        APP_USERNAME=os.environ.get("APP_USERNAME", "quant"),
        APP_PASSWORD=os.environ.get("APP_PASSWORD", ""),
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
    if on_vercel and not app.config["TURSO_DATABASE_URL"]:
        raise RuntimeError("Không tìm thấy cặp biến Turso trên Vercel; SQLite local không lưu bền vững.")
    if on_vercel and not app.config["TURSO_AUTH_TOKEN"]:
        raise RuntimeError("Thiếu token xác thực cho Turso trên Vercel.")
    if on_vercel and not app.config["APP_PASSWORD"]:
        raise RuntimeError("Thiếu APP_PASSWORD trên Vercel; ứng dụng không được triển khai công khai.")

    from . import db

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
