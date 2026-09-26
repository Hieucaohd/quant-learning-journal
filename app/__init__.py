import os
import sys
from pathlib import Path

from flask import Flask


def create_app(test_config=None):
    app = Flask(__name__)
    source_root = Path(__file__).resolve().parent.parent
    data_root = (Path(sys.executable).resolve().parent
                 if getattr(sys, "frozen", False) else source_root)
    app.config.update(
        DATABASE=str(data_root / "data" / "journal.sqlite3"),
        EXPORT_DIR=str(data_root / "exports"),
        BACKUP_DIR=str(data_root / "database" / "backups"),
        SECRET_KEY=os.environ.get("JOURNAL_SECRET_KEY", "local-only-journal-key"),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)

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
