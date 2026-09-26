import sqlite3
from pathlib import Path

from flask import current_app, g


SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    start_date TEXT,
    target_date TEXT,
    status TEXT NOT NULL DEFAULT 'Chưa bắt đầu',
    progress INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100),
    notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS lectures (
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    lecture_number INTEGER NOT NULL CHECK(lecture_number > 0),
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Chưa bắt đầu',
    estimated_hours REAL NOT NULL DEFAULT 0,
    understanding_score INTEGER CHECK(understanding_score BETWEEN 1 AND 10),
    notes TEXT NOT NULL DEFAULT '',
    manual_deadline TEXT,
    UNIQUE(course_id, lecture_number)
);
CREATE TABLE IF NOT EXISTS journals (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE RESTRICT,
    topic TEXT NOT NULL DEFAULT '',
    lecture TEXT NOT NULL DEFAULT '',
    hours REAL NOT NULL CHECK(hours > 0 AND hours <= 24),
    difficulty TEXT NOT NULL DEFAULT 'Trung bình',
    understanding INTEGER CHECK(understanding BETWEEN 1 AND 10),
    notes TEXT NOT NULL DEFAULT '',
    problems TEXT NOT NULL DEFAULT '',
    next_plan TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS journals_date_idx ON journals(date);
CREATE INDEX IF NOT EXISTS journals_course_idx ON journals(course_id);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY,
    period TEXT NOT NULL CHECK(period IN ('week', 'month')),
    period_start TEXT NOT NULL,
    strength TEXT NOT NULL DEFAULT '',
    weakness TEXT NOT NULL DEFAULT '',
    action_plan TEXT NOT NULL DEFAULT '',
    UNIQUE(period, period_start)
);
CREATE TABLE IF NOT EXISTS capacity_profiles (
    id INTEGER PRIMARY KEY,
    effective_from TEXT NOT NULL UNIQUE,
    hours_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS capacity_overrides (
    study_date TEXT PRIMARY KEY,
    hours REAL NOT NULL CHECK(hours >= 0 AND hours <= 24),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS schedule_allocations (
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    lecture_id INTEGER REFERENCES lectures(id) ON DELETE CASCADE,
    study_date TEXT NOT NULL,
    hours REAL NOT NULL CHECK(hours > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS schedule_day_idx ON schedule_allocations(study_date);
CREATE TABLE IF NOT EXISTS schedule_events (
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    lecture_id INTEGER REFERENCES lectures(id) ON DELETE CASCADE,
    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    old_deadline TEXT,
    new_deadline TEXT,
    reason TEXT NOT NULL,
    kind TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS missed_deadlines (
    id INTEGER PRIMARY KEY,
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    old_deadline TEXT NOT NULL,
    new_deadline TEXT,
    reason TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS capacity_events (
    id INTEGER PRIMARY KEY,
    effective_date TEXT NOT NULL,
    old_hours TEXT,
    new_hours TEXT NOT NULL,
    reason TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS lecture_tasks (
    id INTEGER PRIMARY KEY,
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    estimated_hours REAL NOT NULL CHECK(estimated_hours > 0),
    remaining_hours REAL NOT NULL CHECK(remaining_hours >= 0),
    status TEXT NOT NULL DEFAULT 'Chưa bắt đầu',
    deadline TEXT,
    manual_deadline TEXT,
    completed_at TEXT,
    completed_on_time INTEGER,
    UNIQUE(lecture_id, position)
);
CREATE INDEX IF NOT EXISTS lecture_tasks_lecture_idx ON lecture_tasks(lecture_id);
CREATE TABLE IF NOT EXISTS completion_events (
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    task_id INTEGER REFERENCES lecture_tasks(id) ON DELETE CASCADE,
    old_date TEXT,
    new_date TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS completion_events_course_idx ON completion_events(course_id, id);
CREATE TABLE IF NOT EXISTS plan_imports (
    id INTEGER PRIMARY KEY,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_name TEXT NOT NULL DEFAULT '',
    payload_sha256 TEXT NOT NULL,
    course_count INTEGER NOT NULL,
    lecture_count INTEGER NOT NULL,
    task_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS study_plans (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Đang hoạt động',
    priority INTEGER NOT NULL DEFAULT 100,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS plan_courses (
    plan_id INTEGER NOT NULL REFERENCES study_plans(id) ON DELETE CASCADE,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    PRIMARY KEY(plan_id, course_id)
);
CREATE INDEX IF NOT EXISTS plan_courses_course_idx ON plan_courses(course_id);
CREATE TABLE IF NOT EXISTS deletion_backups (
    id INTEGER PRIMARY KEY,
    label TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class DatabaseRow:
    def __init__(self, columns, values):
        self._columns = tuple(columns)
        self._values = tuple(values)
        self._by_name = dict(zip(self._columns, self._values))

    def __getitem__(self, key):
        return self._values[key] if isinstance(key, (int, slice)) else self._by_name[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def keys(self):
        return self._columns


class DatabaseCursor:
    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self._columns = tuple(item[0] for item in (cursor.description or ()))
        self._lastrowid = getattr(cursor, "lastrowid", None) if lastrowid is None else lastrowid

    def _row(self, row):
        return None if row is None else DatabaseRow(self._columns, row)

    def fetchone(self):
        return self._row(self._cursor.fetchone())

    def fetchall(self):
        return [self._row(row) for row in self._cursor.fetchall()]

    def __iter__(self):
        while True:
            row = self._cursor.fetchone()
            if row is None:
                return
            yield self._row(row)

    @property
    def lastrowid(self):
        return self._lastrowid

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class LibsqlConnection:
    """Lớp tương thích sqlite3.Row cho trình điều khiển Turso/libSQL."""

    is_remote = True

    def __init__(self, connection):
        self._connection = connection

    @staticmethod
    def _raise_compatible(error):
        message = str(error)
        if "constraint failed" in message.lower():
            raise sqlite3.IntegrityError(message) from error
        raise sqlite3.DatabaseError(message) from error

    def execute(self, sql, parameters=None):
        try:
            cursor = (self._connection.execute(sql) if parameters is None
                      else self._connection.execute(sql, tuple(parameters)))
            lastrowid = getattr(cursor, "lastrowid", None)
            if sql.lstrip().upper().startswith("INSERT") and not lastrowid:
                lastrowid = self._connection.execute(
                    "SELECT last_insert_rowid()").fetchone()[0]
            return DatabaseCursor(cursor, lastrowid)
        except ValueError as error:
            self._raise_compatible(error)

    def executemany(self, sql, parameters):
        try:
            values = [tuple(item) for item in parameters]
            return DatabaseCursor(self._connection.executemany(sql, values))
        except ValueError as error:
            self._raise_compatible(error)

    def executescript(self, sql):
        try:
            return self._connection.executescript(sql)
        except ValueError as error:
            self._raise_compatible(error)

    def commit(self):
        return self._connection.commit()

    def rollback(self):
        return self._connection.rollback()

    def close(self):
        return self._connection.close()


def get_db():
    if "db" not in g:
        remote_url = current_app.config.get("TURSO_DATABASE_URL")
        if remote_url:
            import libsql

            connection = libsql.connect(
                database=remote_url,
                auth_token=current_app.config.get("TURSO_AUTH_TOKEN", ""),
            )
            g.db = LibsqlConnection(connection)
        else:
            path = Path(current_app.config["DATABASE"])
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            g.db = connection
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_app(app):
    app.teardown_appcontext(close_db)
    if not app.config.get("AUTO_MIGRATE_DATABASE", True):
        return
    with app.app_context():
        db = get_db()
        db.executescript(SCHEMA)
        migrate_schedule()
        migrate_vietnamese()
        migrate_plans()
        get_db().commit()


def migrate_plans():
    db = get_db()
    if db.execute("SELECT 1 FROM study_plans LIMIT 1").fetchone():
        return
    plan_id = db.execute("""INSERT INTO study_plans (name, description, priority)
        VALUES ('Lộ trình Quant', 'Lộ trình học Quant hiện tại', 1)""").lastrowid
    for position, row in enumerate(db.execute("SELECT id FROM courses ORDER BY id"), 1):
        db.execute("INSERT INTO plan_courses (plan_id, course_id, position) VALUES (?, ?, ?)",
                   (plan_id, row["id"], position))


def sync_lecture_hours(db=None, lecture_id=None):
    """Đồng bộ giờ của bài học từ các phần việc; không nhận số giờ nhập tay."""
    db = db or get_db()
    params = () if lecture_id is None else (lecture_id,)
    where = "" if lecture_id is None else " WHERE id=?"
    db.execute(f"""UPDATE lectures SET
        estimated_hours=COALESCE((
            SELECT ROUND(SUM(t.estimated_hours), 2)
            FROM lecture_tasks t WHERE t.lecture_id=lectures.id
        ), 0),
        remaining_hours=COALESCE((
            SELECT ROUND(SUM(CASE WHEN t.status='Hoàn thành' THEN 0
                                  ELSE t.remaining_hours END), 2)
            FROM lecture_tasks t WHERE t.lecture_id=lectures.id
        ), 0){where}""", params)


def migrate_schedule():
    db = get_db()
    course_columns_before = {row["name"] for row in db.execute("PRAGMA table_info(courses)")}
    columns = {
        "courses": {"estimated_hours": "REAL NOT NULL DEFAULT 0"},
        "lectures": {
            "estimated_hours": "REAL NOT NULL DEFAULT 0",
            "remaining_hours": "REAL NOT NULL DEFAULT 0",
            "content": "TEXT NOT NULL DEFAULT ''",
            "deadline": "TEXT",
            "manual_deadline": "TEXT",
            "completed_at": "TEXT",
            "completed_on_time": "INTEGER",
        },
    }
    for table, additions in columns.items():
        existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        for column, definition in additions.items():
            if column not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    sync_lecture_hours(db)
    if "estimated_hours" not in course_columns_before:
        for name, hours in (("MIT 18.02", 120), ("MIT 18.06", 100),
                            ("MIT 6.041", 140), ("MIT Finance MicroMasters", 220)):
            db.execute("""UPDATE courses SET estimated_hours=? WHERE name=? AND
                NOT EXISTS (SELECT 1 FROM lectures WHERE course_id=courses.id) AND
                NOT EXISTS (SELECT 1 FROM journals WHERE course_id=courses.id)""",
                (hours, name))
    extra_columns = {
        "lecture_tasks": {"manual_deadline": "TEXT"},
        "schedule_allocations": {"task_id": "INTEGER REFERENCES lecture_tasks(id) ON DELETE CASCADE"},
        "schedule_events": {"task_id": "INTEGER REFERENCES lecture_tasks(id) ON DELETE CASCADE"},
        "missed_deadlines": {"task_id": "INTEGER REFERENCES lecture_tasks(id) ON DELETE CASCADE"},
    }
    for table, additions in extra_columns.items():
        existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        for column, definition in additions.items():
            if column not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    if not db.execute("SELECT 1 FROM capacity_profiles").fetchone():
        db.execute("""INSERT INTO capacity_profiles (effective_from, hours_json, reason)
            VALUES ('2000-01-01', '[4,4,4,4,4,8,8]', 'Lịch mặc định: 4 giờ ngày thường, 8 giờ cuối tuần')""")


def migrate_vietnamese():
    """Translate legacy English categories and the original demo text in place."""
    db = get_db()
    for table in ("courses", "lectures"):
        for old, new in (("Not started", "Chưa bắt đầu"), ("Learning", "Đang học"),
                         ("Completed", "Hoàn thành"), ("Need review", "Cần ôn tập")):
            db.execute(f"UPDATE {table} SET status = ? WHERE status = ?", (new, old))
    for old, new in (("Easy", "Dễ"), ("Medium", "Trung bình"), ("Hard", "Khó")):
        db.execute("UPDATE journals SET difficulty = ? WHERE difficulty = ?", (new, old))
    for name, old, new in (
        ("MIT 18.01", "Single Variable Calculus", "Giải tích một biến"),
        ("MIT 18.02", "Multivariable Calculus", "Giải tích nhiều biến"),
        ("MIT 18.06", "Linear Algebra", "Đại số tuyến tính"),
        ("MIT 6.041", "Probability", "Xác suất"),
        ("MIT Finance MicroMasters", "Finance curriculum", "Chương trình tài chính"),
    ):
        db.execute("UPDATE courses SET description = ? WHERE name = ? AND description = ?",
                   (new, name, old))
    db.execute("UPDATE lectures SET title = 'Bài ' || lecture_number "
               "WHERE title = 'Lecture ' || lecture_number")
    for column, old, new in (
        ("topic", "Review lectures 1–12", "Ôn tập bài 1–12"),
        ("notes", "Chain rule; optimization; related rates", "Quy tắc dây chuyền; tối ưu hóa; tốc độ liên hệ"),
        ("problems", "Need more practice with implicit differentiation", "Cần luyện thêm đạo hàm ẩn"),
        ("next_plan", "Continue lecture 13", "Tiếp tục bài 13"),
    ):
        db.execute(f"UPDATE journals SET {column} = ? WHERE {column} = ?", (new, old))


def seed_demo():
    db = get_db()
    first_seed = not db.execute("SELECT 1 FROM courses WHERE name = 'MIT 18.01'").fetchone()
    courses = [
        ("MIT 18.01", "Giải tích một biến"),
        ("MIT 18.02", "Giải tích nhiều biến"),
        ("MIT 18.06", "Đại số tuyến tính"),
        ("MIT 6.041", "Xác suất"),
        ("MIT Finance MicroMasters", "Chương trình tài chính"),
    ]
    for name, description in courses:
        db.execute(
            "INSERT OR IGNORE INTO courses (name, description) VALUES (?, ?)",
            (name, description),
        )
    if first_seed:
        for name, hours in (("MIT 18.02", 120), ("MIT 18.06", 100),
                            ("MIT 6.041", 140), ("MIT Finance MicroMasters", 220)):
            db.execute("UPDATE courses SET estimated_hours=? WHERE name=?", (hours, name))
    course_id = db.execute("SELECT id FROM courses WHERE name = 'MIT 18.01'").fetchone()["id"]
    for number in range(1, 39):
        db.execute(
            "INSERT OR IGNORE INTO lectures (course_id, lecture_number, title) VALUES (?, ?, ?)",
            (course_id, number, f"Bài {number}"),
        )
    if first_seed:
        db.execute("UPDATE courses SET status = 'Đang học' WHERE id = ?", (course_id,))
        db.execute("UPDATE lectures SET status = 'Hoàn thành' WHERE course_id = ? AND lecture_number <= 12",
                   (course_id,))
        plan = db.execute("SELECT id FROM study_plans ORDER BY id LIMIT 1").fetchone()
        if plan:
            for position, (name, _description) in enumerate(courses, 1):
                seeded_course = db.execute("SELECT id FROM courses WHERE name=?", (name,)).fetchone()
                db.execute("""INSERT OR IGNORE INTO plan_courses (plan_id, course_id, position)
                    VALUES (?, ?, ?)""", (plan["id"], seeded_course["id"], position))
    sample_key = ("2026-09-19", course_id, "Ôn tập bài 1–12")
    if not db.execute(
        "SELECT 1 FROM journals WHERE date = ? AND course_id = ? AND topic = ?",
        sample_key,
    ).fetchone():
        db.execute(
            """INSERT INTO journals
            (date, course_id, topic, lecture, hours, difficulty, understanding, notes, problems, next_plan)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "2026-09-19", course_id, "Ôn tập bài 1–12", "1–12", 8,
                "Trung bình", 7, "Quy tắc dây chuyền; tối ưu hóa; tốc độ liên hệ",
                "Cần luyện thêm đạo hàm ẩn", "Tiếp tục bài 13",
            ),
        )
    db.commit()
