"""Upgrade the configured database schema. Run by hand; never automatic on Turso.

    python scripts/migrate_database.py          # show which database and versions
    python scripts/migrate_database.py --yes    # actually migrate it
"""
import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env", override=False)

from app import create_app  # noqa: E402
from app.db import SCHEMA_VERSION, migrate_database, stored_schema_version  # noqa: E402


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Nâng cấp schema của database đang cấu hình.")
    parser.add_argument("--yes", action="store_true", help="Xác nhận chạy migration")
    args = parser.parse_args()

    app = create_app({"AUTO_MIGRATE_DATABASE": False})
    url = app.config["TURSO_DATABASE_URL"]
    target = urlparse(url).hostname or url if url else app.config["DATABASE"]
    with app.app_context():
        version = stored_schema_version()
        print(f"Database: {target}")
        print(f"Schema hiện tại: v{version} · code cần: v{SCHEMA_VERSION}")
        if version is not None and version >= SCHEMA_VERSION:
            print("Không cần migration.")
            return
        if not args.yes:
            print("Hãy sao lưu database trước, rồi chạy lại với --yes để migration.")
            return
        migrate_database()
        print(f"Đã nâng cấp lên v{stored_schema_version()}.")


if __name__ == "__main__":
    main()
