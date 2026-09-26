import argparse
import os
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from app.db import get_db


TABLES = (
    "courses",
    "lectures",
    "journals",
    "reviews",
    "capacity_profiles",
    "capacity_overrides",
    "lecture_tasks",
    "study_plans",
    "plan_courses",
    "schedule_allocations",
    "schedule_events",
    "missed_deadlines",
    "capacity_events",
    "completion_events",
    "plan_imports",
    "deletion_backups",
)


def columns(connection, table):
    return [row["name"] for row in connection.execute(f"PRAGMA table_info({table})")]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Chuyển dữ liệu SQLite local sang một Turso database đang trống.")
    parser.add_argument("--source", default=str(PROJECT_ROOT / "data" / "journal.sqlite3"))
    args = parser.parse_args()

    source_path = Path(args.source).resolve()
    if not source_path.is_file():
        raise SystemExit(f"Không tìm thấy SQLite: {source_path}")
    url = os.environ.get("TURSO_DATABASE_URL", "")
    token = os.environ.get("TURSO_AUTH_TOKEN", "")
    if not url or not token:
        raise SystemExit("Hãy đặt TURSO_DATABASE_URL và TURSO_AUTH_TOKEN trước khi chạy.")

    app = create_app({
        "TURSO_DATABASE_URL": url,
        "TURSO_AUTH_TOKEN": token,
        "APP_PASSWORD": "",
        "TESTING": True,
    })
    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    try:
        with app.app_context():
            target = get_db()
            try:
                if target.execute("SELECT COUNT(*) FROM courses").fetchone()[0]:
                    raise SystemExit("Turso đã có khóa học. Script dừng để tránh ghi đè dữ liệu.")

                for table in reversed(TABLES):
                    target.execute(f"DELETE FROM {table}")

                copied = {}
                for table in TABLES:
                    source_columns = [row["name"] for row in source.execute(
                        f"PRAGMA table_info({table})")]
                    target_columns = set(columns(target, table))
                    selected = [name for name in source_columns if name in target_columns]
                    if not selected:
                        copied[table] = 0
                        continue
                    rows = source.execute(
                        f"SELECT {', '.join(selected)} FROM {table}").fetchall()
                    placeholders = ", ".join("?" for _ in selected)
                    sql = (f"INSERT INTO {table} ({', '.join(selected)}) "
                           f"VALUES ({placeholders})")
                    for row in rows:
                        target.execute(sql, tuple(row[name] for name in selected))
                    copied[table] = len(rows)
                target.commit()
            except BaseException:
                target.rollback()
                raise
    finally:
        source.close()

    print(f"Đã chuyển dữ liệu từ {source_path} sang Turso:")
    for table, count in copied.items():
        print(f"- {table}: {count}")


if __name__ == "__main__":
    main()
