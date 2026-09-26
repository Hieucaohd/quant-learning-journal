import os
import sys
from hmac import compare_digest
from pathlib import Path

from flask import Flask, Response, request


def create_app(test_config=None):
    app = Flask(__name__)
    source_root = Path(__file__).resolve().parent.parent
    on_vercel = bool(os.environ.get("VERCEL"))
    data_root = (Path(sys.executable).resolve().parent
                 if getattr(sys, "frozen", False) else source_root)
    app.config.update(
        DATABASE=str(data_root / "data" / "journal.sqlite3"),
        EXPORT_DIR=str(Path("/tmp/exports") if on_vercel else data_root / "exports"),
        BACKUP_DIR=str(Path("/tmp/backups") if on_vercel else data_root / "database" / "backups"),
        TURSO_DATABASE_URL=os.environ.get("TURSO_DATABASE_URL", ""),
        TURSO_AUTH_TOKEN=os.environ.get("TURSO_AUTH_TOKEN", ""),
        APP_USERNAME=os.environ.get("APP_USERNAME", "quant"),
        APP_PASSWORD=os.environ.get("APP_PASSWORD", ""),
        SECRET_KEY=os.environ.get("JOURNAL_SECRET_KEY", "local-only-journal-key"),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    if on_vercel and not app.config["TURSO_DATABASE_URL"]:
        raise RuntimeError("Thiếu TURSO_DATABASE_URL trên Vercel; SQLite local không lưu bền vững.")
    if on_vercel and not app.config["APP_PASSWORD"]:
        raise RuntimeError("Thiếu APP_PASSWORD trên Vercel; ứng dụng không được triển khai công khai.")

    @app.before_request
    def require_password():
        password = app.config.get("APP_PASSWORD", "")
        if not password:
            return None
        auth = request.authorization
        valid = (auth and compare_digest(auth.username or "", app.config["APP_USERNAME"])
                 and compare_digest(auth.password or "", password))
        if valid:
            return None
        return Response("Cần đăng nhập để mở Nhật ký học Quant.", 401,
                        {"WWW-Authenticate": 'Basic realm="Quant Learning Journal"'})

    from . import db

    db.init_app(app)
    from .routes import bp

    app.register_blueprint(bp)

    @app.errorhandler(404)
    def not_found(_error):
        return "Không tìm thấy trang hoặc mục dữ liệu được yêu cầu.", 404

    @app.errorhandler(400)
    def bad_request(_error):
        return "Yêu cầu không hợp lệ.", 400

    @app.errorhandler(413)
    def too_large(_error):
        return "File JSON quá lớn (giới hạn 2 MB).", 413

    return app
