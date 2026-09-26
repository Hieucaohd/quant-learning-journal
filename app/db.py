import json
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from flask import current_app, g
from werkzeug.security import generate_password_hash


SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user', 'admin')),
    display_name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS course_catalogs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
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
CREATE TABLE IF NOT EXISTS plan_shares (
    plan_id INTEGER NOT NULL REFERENCES study_plans(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    shared_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(plan_id, user_id)
);
CREATE TABLE IF NOT EXISTS user_capacity_profiles (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    effective_from TEXT NOT NULL,
    hours_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, effective_from)
);
CREATE TABLE IF NOT EXISTS user_capacity_overrides (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    study_date TEXT NOT NULL,
    hours REAL NOT NULL CHECK(hours >= 0 AND hours <= 24),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, study_date)
);
CREATE TABLE IF NOT EXISTS user_capacity_events (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    effective_date TEXT NOT NULL,
    old_hours TEXT,
    new_hours TEXT NOT NULL,
    reason TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS user_reviews (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    period TEXT NOT NULL CHECK(period IN ('week', 'month')),
    period_start TEXT NOT NULL,
    strength TEXT NOT NULL DEFAULT '',
    weakness TEXT NOT NULL DEFAULT '',
    action_plan TEXT NOT NULL DEFAULT '',
    UNIQUE(user_id, period, period_start)
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
        with app.app_context():
            version = stored_schema_version()
            if version is None or version < SCHEMA_VERSION:
                app.logger.warning(
                    "Database schema v%s is older than the code (v%s); run "
                    "scripts/migrate_database.py to upgrade it.", version, SCHEMA_VERSION)
        return
    with app.app_context():
        db = get_db()
        db.execute("CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        version = db.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()
        if (current_app.config.get("TESTING") or version is None
                or int(version["value"]) < SCHEMA_VERSION):
            migrate_database()
        claim_passwordless_admin()


def stored_schema_version():
    """Read-only: the schema version recorded in the database, or None."""
    try:
        row = get_db().execute(
            "SELECT value FROM app_meta WHERE key='schema_version'").fetchone()
    except sqlite3.Error:
        return None
    return int(row["value"]) if row else None


def claim_passwordless_admin():
    """Give a password-less first admin the configured APP_PASSWORD.

    A first user without a password makes /setup open to whoever visits first.
    That is only acceptable for a local install; when APP_PASSWORD is set (as it
    must be on Vercel) the account is secured at startup instead.
    """
    password = current_app.config.get("APP_PASSWORD") or ""
    if not password:
        return
    db = get_db()
    admin = db.execute("SELECT id, password_hash FROM users ORDER BY id LIMIT 1").fetchone()
    if admin and not admin["password_hash"]:
        db.execute("UPDATE users SET password_hash=? WHERE id=?",
                   (generate_password_hash(password), admin["id"]))
        db.commit()


def _columns(table):
    return {row["name"] for row in get_db().execute(f"PRAGMA table_info({table})")}


def _add_column(table, name, definition):
    if name not in _columns(table):
        get_db().execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def ensure_user_defaults(user_id):
    db = get_db()
    if not db.execute("SELECT 1 FROM user_capacity_profiles WHERE user_id=? LIMIT 1",
                      (user_id,)).fetchone():
        db.execute("""INSERT INTO user_capacity_profiles
            (user_id, effective_from, hours_json, reason)
            VALUES (?, '2000-01-01', '[4,4,4,4,4,8,8]',
                    'Lịch mặc định: 4 giờ ngày thường, 8 giờ cuối tuần')""", (user_id,))


def create_user(username, password, role="user", display_name=""):
    username = username.strip()
    if not username or len(username) > 80:
        raise ValueError("Tên đăng nhập phải có từ 1 đến 80 ký tự")
    if len(password) < 8:
        raise ValueError("Mật khẩu phải có ít nhất 8 ký tự")
    if role not in ("user", "admin"):
        raise ValueError("Vai trò không hợp lệ")
    db = get_db()
    user_id = db.execute("""INSERT INTO users
        (username, password_hash, role, display_name) VALUES (?, ?, ?, ?)""",
        (username, generate_password_hash(password), role,
         display_name.strip() or username)).lastrowid
    ensure_user_defaults(user_id)
    return user_id


def delete_user_account(user_id, actor_id):
    """Delete a user and everything they own, after saving it to deletion_backups.

    Rows are deleted explicitly rather than through ON DELETE CASCADE because the
    remote libSQL connection may not enforce foreign keys. The caller commits.
    Returns the id of the backup row.
    """
    db = get_db()
    course_scope = "SELECT id FROM courses WHERE owner_user_id=?"
    lecture_scope = f"SELECT id FROM lectures WHERE course_id IN ({course_scope})"
    plan_scope = "SELECT id FROM study_plans WHERE owner_user_id=?"
    owned = {
        "users": ("SELECT * FROM users WHERE id=?", 1),
        "courses": ("SELECT * FROM courses WHERE owner_user_id=?", 1),
        "lectures": (f"SELECT * FROM lectures WHERE course_id IN ({course_scope})", 1),
        "lecture_tasks": (f"SELECT * FROM lecture_tasks WHERE lecture_id IN ({lecture_scope})", 1),
        "journals": (f"SELECT * FROM journals WHERE user_id=? OR course_id IN ({course_scope})", 2),
        "study_plans": ("SELECT * FROM study_plans WHERE owner_user_id=?", 1),
        "plan_courses": (f"""SELECT * FROM plan_courses WHERE plan_id IN ({plan_scope})
            OR course_id IN ({course_scope})""", 2),
        "plan_shares": (f"SELECT * FROM plan_shares WHERE user_id=? OR plan_id IN ({plan_scope})", 2),
        "schedule_allocations": (
            f"SELECT * FROM schedule_allocations WHERE course_id IN ({course_scope})", 1),
        "schedule_events": (f"SELECT * FROM schedule_events WHERE course_id IN ({course_scope})", 1),
        "completion_events": (
            f"SELECT * FROM completion_events WHERE course_id IN ({course_scope})", 1),
        "missed_deadlines": (
            f"SELECT * FROM missed_deadlines WHERE lecture_id IN ({lecture_scope})", 1),
        "user_capacity_profiles": ("SELECT * FROM user_capacity_profiles WHERE user_id=?", 1),
        "user_capacity_overrides": ("SELECT * FROM user_capacity_overrides WHERE user_id=?", 1),
        "user_capacity_events": ("SELECT * FROM user_capacity_events WHERE user_id=?", 1),
        "user_reviews": ("SELECT * FROM user_reviews WHERE user_id=?", 1),
        "plan_imports": ("SELECT * FROM plan_imports WHERE user_id=?", 1),
    }
    snapshot = {table: [dict(row) for row in db.execute(sql, (user_id,) * count)]
                for table, (sql, count) in owned.items()}
    user = snapshot["users"][0]
    backup_id = db.execute("""INSERT INTO deletion_backups (label, payload_json, user_id)
        VALUES (?, ?, ?)""", (f"user-{user['username']}",
                              json.dumps(snapshot, ensure_ascii=False, default=str),
                              actor_id)).lastrowid
    # Children first, so the order is valid whether or not foreign keys are enforced.
    for table in ("journals", "plan_courses", "plan_shares", "missed_deadlines",
                  "schedule_allocations", "schedule_events", "completion_events",
                  "lecture_tasks", "lectures", "courses", "study_plans",
                  "user_capacity_profiles", "user_capacity_overrides",
                  "user_capacity_events", "user_reviews", "plan_imports"):
        sql, count = owned[table]
        db.execute(sql.replace("SELECT *", "DELETE", 1), (user_id,) * count)
    db.execute("UPDATE course_catalogs SET created_by=NULL WHERE created_by=?", (user_id,))
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    return backup_id


def _unique_course_name(display_name, user_id):
    db = get_db()
    if not db.execute("SELECT 1 FROM courses WHERE name=?", (display_name,)).fetchone():
        return display_name
    return f"{display_name} [u{user_id}-{uuid4().hex[:8]}]"


def enroll_catalog_course(user_id, catalog_id, clone_content=True):
    """Tạo bản tiến độ riêng từ một khóa học trong danh mục chung."""
    db = get_db()
    existing = db.execute("""SELECT id FROM courses
        WHERE owner_user_id=? AND catalog_course_id=?""", (user_id, catalog_id)).fetchone()
    if existing:
        return existing["id"]
    catalog = db.execute("SELECT * FROM course_catalogs WHERE id=?", (catalog_id,)).fetchone()
    if catalog is None:
        raise ValueError("Không tìm thấy khóa học trong danh mục chung")
    source = db.execute("""SELECT * FROM courses WHERE catalog_course_id=?
        ORDER BY owner_user_id, id LIMIT 1""", (catalog_id,)).fetchone()
    estimated = source["estimated_hours"] if source else 0
    course_id = db.execute("""INSERT INTO courses
        (name, display_name, description, estimated_hours, owner_user_id, catalog_course_id)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (_unique_course_name(catalog["name"], user_id), catalog["name"],
         catalog["description"], estimated, user_id, catalog_id)).lastrowid
    if source and clone_content:
        for lecture in db.execute("""SELECT * FROM lectures WHERE course_id=?
            ORDER BY lecture_number""", (source["id"],)).fetchall():
            lecture_id = db.execute("""INSERT INTO lectures
                (course_id, lecture_number, title, estimated_hours, remaining_hours, content)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (course_id, lecture["lecture_number"], lecture["title"],
                 lecture["estimated_hours"], lecture["estimated_hours"], lecture["content"])).lastrowid
            for task in db.execute("""SELECT * FROM lecture_tasks WHERE lecture_id=?
                ORDER BY position""", (lecture["id"],)).fetchall():
                db.execute("""INSERT INTO lecture_tasks
                    (lecture_id, position, kind, title, content, estimated_hours, remaining_hours)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (lecture_id, task["position"], task["kind"], task["title"],
                     task["content"], task["estimated_hours"], task["estimated_hours"]))
    return course_id


def migrate_multi_user():
    db = get_db()
    username = current_app.config.get("APP_USERNAME") or "admin"
    password = current_app.config.get("APP_PASSWORD") or ""
    if not db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        password_hash = generate_password_hash(password) if password else ""
        db.execute("""INSERT INTO users (username, password_hash, role, display_name)
            VALUES (?, ?, 'admin', ?)""", (username, password_hash, username))
    admin_id = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()[0]

    for table in ("courses", "study_plans"):
        _add_column(table, "owner_user_id",
                    f"INTEGER NOT NULL DEFAULT {admin_id}")
    _add_column("courses", "display_name", "TEXT NOT NULL DEFAULT ''")
    _add_column("courses", "catalog_course_id", "INTEGER")
    for table in ("journals", "plan_imports", "deletion_backups"):
        _add_column(table, "user_id",
                    f"INTEGER NOT NULL DEFAULT {admin_id}")
    db.execute("UPDATE courses SET display_name=name WHERE display_name='' OR display_name IS NULL")

    for course in db.execute("SELECT id, display_name, description, owner_user_id, catalog_course_id FROM courses"):
        if course["catalog_course_id"] is not None:
            continue
        db.execute("""INSERT OR IGNORE INTO course_catalogs (name, description, created_by)
            VALUES (?, ?, ?)""",
            (course["display_name"], course["description"], course["owner_user_id"]))
        catalog = db.execute("SELECT id FROM course_catalogs WHERE name=?",
                             (course["display_name"],)).fetchone()
        db.execute("UPDATE courses SET catalog_course_id=? WHERE id=?",
                   (catalog["id"], course["id"]))

    ensure_user_defaults(admin_id)
    if (not db.execute("SELECT 1 FROM user_capacity_profiles WHERE user_id=? AND id!=1 LIMIT 1",
                       (admin_id,)).fetchone()
            and db.execute("SELECT 1 FROM capacity_profiles LIMIT 1").fetchone()):
        for row in db.execute("SELECT effective_from, hours_json, reason, created_at FROM capacity_profiles"):
            db.execute("""INSERT OR IGNORE INTO user_capacity_profiles
                (user_id, effective_from, hours_json, reason, created_at)
                VALUES (?, ?, ?, ?, ?)""",
                (admin_id, row["effective_from"], row["hours_json"], row["reason"], row["created_at"]))
    if not db.execute("SELECT 1 FROM user_capacity_overrides WHERE user_id=?", (admin_id,)).fetchone():
        for row in db.execute("SELECT * FROM capacity_overrides"):
            db.execute("""INSERT OR IGNORE INTO user_capacity_overrides
                (user_id, study_date, hours, reason, created_at) VALUES (?, ?, ?, ?, ?)""",
                (admin_id, row["study_date"], row["hours"], row["reason"], row["created_at"]))
    if not db.execute("SELECT 1 FROM user_capacity_events WHERE user_id=?", (admin_id,)).fetchone():
        for row in db.execute("SELECT * FROM capacity_events"):
            db.execute("""INSERT INTO user_capacity_events
                (user_id, effective_date, old_hours, new_hours, reason, kind, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (admin_id, row["effective_date"], row["old_hours"], row["new_hours"],
                 row["reason"], row["kind"], row["created_at"]))
    if not db.execute("SELECT 1 FROM user_reviews WHERE user_id=?", (admin_id,)).fetchone():
        for row in db.execute("SELECT * FROM reviews"):
            db.execute("""INSERT OR IGNORE INTO user_reviews
                (user_id, period, period_start, strength, weakness, action_plan)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (admin_id, row["period"], row["period_start"], row["strength"],
                 row["weakness"], row["action_plan"]))


def migrate_database():
    db = get_db()
    db.executescript(SCHEMA)
    migrate_schedule()
    migrate_multi_user()
    migrate_vietnamese()
    migrate_plans()
    db.commit()
    migrate_plan_name_scope()
    db.execute("""INSERT INTO app_meta (key, value) VALUES ('schema_version', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (str(SCHEMA_VERSION),))
    db.commit()


def migrate_plans():
    db = get_db()
    if db.execute("SELECT 1 FROM study_plans LIMIT 1").fetchone():
        return
    admin_id = db.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()[0]
    plan_id = db.execute("""INSERT INTO study_plans (name, description, priority, owner_user_id)
        VALUES ('Lộ trình Quant', 'Lộ trình học Quant hiện tại', 1, ?)""",
        (admin_id,)).lastrowid
    for position, row in enumerate(db.execute(
            "SELECT id FROM courses WHERE owner_user_id=? ORDER BY id", (admin_id,)), 1):
        db.execute("INSERT INTO plan_courses (plan_id, course_id, position) VALUES (?, ?, ?)",
                   (plan_id, row["id"], position))


def migrate_plan_name_scope():
    """Plan names were globally unique; make them unique per owner instead.

    SQLite cannot drop a column constraint, so the table is rebuilt. Dropping the
    old table would cascade into plan_courses and plan_shares, so foreign keys are
    turned off; because a remote libSQL connection may not honour that pragma, the
    child rows are also copied aside and restored in the same transaction. The
    caller must have committed first: the pragma is ignored inside a transaction.
    """
    db = get_db()
    table_sql = db.execute("""SELECT sql FROM sqlite_master
        WHERE type='table' AND name='study_plans'""").fetchone()["sql"]
    if "UNIQUE(owner_user_id, name)" in table_sql:
        return
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.executescript("""
            BEGIN;
            CREATE TABLE study_plans_new (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Đang hoạt động',
                priority INTEGER NOT NULL DEFAULT 100,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(owner_user_id, name)
            );
            INSERT INTO study_plans_new
                (id, name, description, status, priority, created_at, owner_user_id)
                SELECT id, name, description, status, priority, created_at, owner_user_id
                FROM study_plans;
            CREATE TABLE plan_courses_keep AS SELECT plan_id, course_id, position FROM plan_courses;
            CREATE TABLE plan_shares_keep AS SELECT plan_id, user_id, shared_at FROM plan_shares;
            DROP TABLE study_plans;
            ALTER TABLE study_plans_new RENAME TO study_plans;
            INSERT OR IGNORE INTO plan_courses (plan_id, course_id, position)
                SELECT plan_id, course_id, position FROM plan_courses_keep;
            INSERT OR IGNORE INTO plan_shares (plan_id, user_id, shared_at)
                SELECT plan_id, user_id, shared_at FROM plan_shares_keep;
            DROP TABLE plan_courses_keep;
            DROP TABLE plan_shares_keep;
            COMMIT;
        """)
    finally:
        db.execute("PRAGMA foreign_keys = ON")


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
        db.execute("""INSERT OR IGNORE INTO course_catalogs (name, description, created_by)
            VALUES (?, ?, 1)""", (name, description))
        catalog_id = db.execute("SELECT id FROM course_catalogs WHERE name=?", (name,)).fetchone()[0]
        db.execute(
            """INSERT OR IGNORE INTO courses
            (name, display_name, description, owner_user_id, catalog_course_id)
            VALUES (?, ?, ?, 1, ?)""",
            (name, name, description, catalog_id),
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
