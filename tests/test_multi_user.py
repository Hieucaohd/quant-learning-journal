import sqlite3
import tempfile
from contextlib import closing
import unittest
from pathlib import Path

from app import create_app, is_remote_database
from app.db import get_db


class MultiUserTest(unittest.TestCase):
    def make_app(self, directory):
        return create_app({
            "TESTING": True,
            "AUTH_DISABLED": False,
            "DATABASE": str(Path(directory) / "journal.sqlite3"),
            "APP_USERNAME": "admin",
            "APP_PASSWORD": "admin-password",
        })

    @staticmethod
    def login(client, username, password):
        return client.post("/login", data={"username": username, "password": password})

    def test_accounts_catalog_isolation_sharing_and_admin_visibility(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory)
            client = app.test_client()
            self.assertEqual(client.get("/").status_code, 302)
            self.login(client, "admin", "admin-password")
            client.post("/courses", data={
                "name": "Toán chung", "description": "Nền tảng", "estimated_hours": "0",
            })
            with app.app_context():
                db = get_db()
                admin_course = db.execute(
                    "SELECT id FROM courses WHERE owner_user_id=1").fetchone()[0]
                db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, title, estimated_hours, remaining_hours)
                    VALUES (?, 1, 'Bài chung', 2, 2)""", (admin_course,))
                db.commit()
            for username in ("bob", "alice", "charlie"):
                self.assertEqual(client.post("/admin/users", data={
                    "username": username, "password": f"{username}-password", "role": "user",
                }).status_code, 302)

            client.post("/logout")
            self.login(client, "bob", "bob-password")
            self.assertEqual(client.get(f"/courses/{admin_course}").status_code, 404)
            with app.app_context():
                catalog_id = get_db().execute(
                    "SELECT id FROM course_catalogs WHERE name='Toán chung'").fetchone()[0]
            self.assertEqual(client.post(f"/catalog/{catalog_id}/enroll").status_code, 302)
            self.assertEqual(client.post("/plans", data={
                "name": "Kế hoạch của Bob", "priority": "1",
            }).status_code, 302)
            with app.app_context():
                db = get_db()
                bob = db.execute("SELECT id FROM users WHERE username='bob'").fetchone()[0]
                alice = db.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
                plan_id = db.execute(
                    "SELECT id FROM study_plans WHERE owner_user_id=?", (bob,)).fetchone()[0]
                bob_course = db.execute(
                    "SELECT id FROM courses WHERE owner_user_id=?", (bob,)).fetchone()[0]
            client.post(f"/plans/{plan_id}/courses", data={"course_id": bob_course})
            client.post(f"/plans/{plan_id}/share", data={"username": "alice"})

            client.post("/logout")
            self.login(client, "alice", "alice-password")
            self.assertEqual(client.get(f"/shared/plans/{plan_id}").status_code, 200)
            self.assertEqual(client.get(f"/plans/{plan_id}").status_code, 404)

            client.post("/logout")
            self.login(client, "charlie", "charlie-password")
            self.assertEqual(client.get(f"/shared/plans/{plan_id}").status_code, 403)

            client.post("/logout")
            self.login(client, "admin", "admin-password")
            self.assertEqual(client.get(f"/shared/plans/{plan_id}").status_code, 200)
            page = client.get(f"/admin/users/{alice}/progress")
            self.assertEqual(page.status_code, 200)

    def test_plan_names_are_unique_per_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory)
            client = app.test_client()
            self.login(client, "admin", "admin-password")
            client.post("/admin/users", data={
                "username": "bob", "password": "bob-password", "role": "user"})
            client.post("/plans", data={"name": "Nền tảng", "priority": "1"})
            client.post("/logout")
            self.login(client, "bob", "bob-password")
            client.post("/plans", data={"name": "Nền tảng", "priority": "1"})
            client.post("/plans", data={"name": "Nền tảng", "priority": "2"})
            with app.app_context():
                owners = [row[0] for row in get_db().execute(
                    "SELECT owner_user_id FROM study_plans WHERE name='Nền tảng' ORDER BY id")]
            self.assertEqual(len(owners), 2)
            self.assertNotEqual(owners[0], owners[1])

    def test_legacy_plan_table_is_rebuilt_without_losing_links(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory)
            with app.app_context():
                db = get_db()
                course_id = db.execute("""INSERT INTO courses (name, display_name, owner_user_id)
                    VALUES ('Khóa cũ', 'Khóa cũ', 1)""").lastrowid
                plan_id = db.execute("SELECT id FROM study_plans").fetchone()[0]
                db.execute("INSERT INTO plan_courses (plan_id, course_id, position) VALUES (?, ?, 1)",
                           (plan_id, course_id))
                db.commit()
                # Recreate the pre-v3 table shape: globally unique plan names.
                db.execute("PRAGMA foreign_keys = OFF")
                db.executescript("""
                    CREATE TABLE legacy AS SELECT * FROM study_plans;
                    DROP TABLE study_plans;
                    CREATE TABLE study_plans (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                        description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL
                        DEFAULT 'Đang hoạt động', priority INTEGER NOT NULL DEFAULT 100,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        owner_user_id INTEGER NOT NULL DEFAULT 1);
                    INSERT INTO study_plans SELECT * FROM legacy;
                    DROP TABLE legacy;
                """)
                db.execute("PRAGMA foreign_keys = ON")
            app = self.make_app(directory)
            with app.app_context():
                db = get_db()
                table_sql = db.execute("""SELECT sql FROM sqlite_master
                    WHERE name='study_plans'""").fetchone()[0]
                self.assertIn("UNIQUE(owner_user_id, name)", table_sql)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM plan_courses WHERE plan_id=?",
                                            (plan_id,)).fetchone()[0], 1)

    def test_shared_view_shows_progress_computed_from_hours(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory)
            client = app.test_client()
            self.login(client, "admin", "admin-password")
            client.post("/admin/users", data={
                "username": "bob", "password": "bob-password", "role": "user"})
            with app.app_context():
                db = get_db()
                course_id = db.execute("""INSERT INTO courses (name, display_name, owner_user_id)
                    VALUES ('Xác suất', 'Xác suất', 1)""").lastrowid
                db.execute("""INSERT INTO lectures (course_id, lecture_number, status,
                    estimated_hours, remaining_hours) VALUES (?, 1, 'Hoàn thành', 3, 0),
                    (?, 2, 'Chưa bắt đầu', 1, 1)""", (course_id, course_id))
                plan_id = db.execute("SELECT id FROM study_plans WHERE owner_user_id=1").fetchone()[0]
                db.execute("INSERT INTO plan_courses (plan_id, course_id, position) VALUES (?, ?, 9)",
                           (plan_id, course_id))
                db.commit()
            client.post(f"/plans/{plan_id}/share", data={"username": "bob"})
            client.post("/logout")
            self.login(client, "bob", "bob-password")
            page = client.get(f"/shared/plans/{plan_id}").get_data(as_text=True)
            self.assertIn("75%", page)
            self.assertIn("Còn 1/4 giờ", page)

    def test_shared_plans_appear_read_only_on_schedule_with_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory)
            client = app.test_client()
            self.login(client, "admin", "admin-password")
            for username in ("bob", "alice", "charlie"):
                client.post("/admin/users", data={
                    "username": username, "password": f"{username}-password", "role": "user"})
            client.post("/logout")

            self.login(client, "bob", "bob-password")
            client.post("/courses", data={"name": "Đại số của Bob", "estimated_hours": "0"})
            client.post("/plans", data={"name": "Lịch của Bob", "priority": "1"})
            with app.app_context():
                db = get_db()
                bob = db.execute("SELECT id FROM users WHERE username='bob'").fetchone()[0]
                course_id = db.execute(
                    "SELECT id FROM courses WHERE owner_user_id=?", (bob,)).fetchone()[0]
                plan_id = db.execute(
                    "SELECT id FROM study_plans WHERE owner_user_id=?", (bob,)).fetchone()[0]
                lecture_id = db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, title, estimated_hours, remaining_hours)
                    VALUES (?, 1, 'Ma trận', 2, 2)""", (course_id,)).lastrowid
                db.commit()
            client.post(f"/plans/{plan_id}/courses", data={"course_id": course_id})
            client.post(f"/plans/{plan_id}/share", data={"username": "alice"})
            client.post("/logout")

            complete_action = f"/lectures/{lecture_id}/complete"
            self.login(client, "alice", "alice-password")
            page = client.get("/schedule").get_data(as_text=True)
            self.assertIn("Lịch của Bob · @bob", page)
            self.assertIn("@bob · chỉ xem", page)
            self.assertIn("Ma trận", page)
            self.assertNotIn(complete_action, page)
            self.assertIn("+2 giờ được chia sẻ", page)
            filtered = client.get("/schedule?filter=1").get_data(as_text=True)
            self.assertNotIn("Ma trận", filtered)
            client.post("/logout")

            self.login(client, "charlie", "charlie-password")
            self.assertNotIn("Lịch của Bob", client.get("/schedule").get_data(as_text=True))
            self.assertEqual(client.get(f"/schedule?filter=1&plan_id={plan_id}").status_code, 400)
            client.post("/logout")

            self.login(client, "admin", "admin-password")
            page = client.get("/schedule").get_data(as_text=True)
            self.assertIn("Người dùng khác (quản trị)", page)
            self.assertNotIn("Ma trận", page)
            page = client.get(f"/schedule?filter=1&plan_id={plan_id}").get_data(as_text=True)
            self.assertIn("Ma trận", page)
            self.assertNotIn(complete_action, page)
            client.post("/logout")

            self.login(client, "bob", "bob-password")
            self.assertIn(complete_action, client.get("/schedule").get_data(as_text=True))

    def test_passwordless_admin_is_claimed_by_app_password(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "journal.sqlite3")
            create_app({"TESTING": True, "AUTH_DISABLED": False, "DATABASE": path,
                        "APP_USERNAME": "admin", "APP_PASSWORD": ""})
            app = self.make_app(directory)
            client = app.test_client()
            self.assertEqual(client.get("/setup").status_code, 302)
            self.assertEqual(client.get("/login").status_code, 200)
            self.assertEqual(self.login(client, "admin", "admin-password").status_code, 302)

    def test_admin_can_rename_and_delete_accounts_with_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory)
            client = app.test_client()
            self.login(client, "admin", "admin-password")
            for username in ("bob", "alice"):
                client.post("/admin/users", data={
                    "username": username, "password": f"{username}-password", "role": "user"})
            with app.app_context():
                db = get_db()
                bob, alice = (db.execute("SELECT id FROM users WHERE username=?", (name,))
                              .fetchone()[0] for name in ("bob", "alice"))
            client.post(f"/admin/users/{bob}/display-name", data={"display_name": "Phượng Anh"})
            page = client.get("/admin/users").get_data(as_text=True)
            self.assertIn("Phượng Anh", page)
            self.assertIn("@bob", page)

            client.post("/logout")
            self.login(client, "bob", "bob-password")
            client.post("/courses", data={"name": "Khóa của Bob", "estimated_hours": "5"})
            client.post("/plans", data={"name": "Kế hoạch Bob", "priority": "1"})
            with app.app_context():
                db = get_db()
                course_id = db.execute("SELECT id FROM courses WHERE owner_user_id=?",
                                       (bob,)).fetchone()[0]
                plan_id = db.execute("SELECT id FROM study_plans WHERE owner_user_id=?",
                                     (bob,)).fetchone()[0]
            client.post(f"/plans/{plan_id}/courses", data={"course_id": course_id})
            client.post(f"/plans/{plan_id}/share", data={"username": "alice"})
            client.post("/journal", data={"date": "2026-09-20", "course_id": course_id,
                                          "hours": "1", "difficulty": "Dễ"})
            client.post("/logout")

            self.login(client, "admin", "admin-password")
            client.post(f"/admin/users/{bob}/delete", data={"confirm_username": "bobx"})
            client.post("/admin/users/1/delete", data={"confirm_username": "admin"})
            with app.app_context():
                self.assertEqual(get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0], 3)
            client.post(f"/admin/users/{bob}/delete", data={"confirm_username": "bob"})
            with app.app_context():
                db = get_db()
                self.assertIsNone(db.execute("SELECT 1 FROM users WHERE id=?", (bob,)).fetchone())
                for table, column in (("courses", "owner_user_id"), ("study_plans", "owner_user_id"),
                                      ("journals", "user_id"), ("user_capacity_profiles", "user_id")):
                    self.assertEqual(db.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (bob,)).fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM plan_shares").fetchone()[0], 0)
                self.assertIsNotNone(db.execute("SELECT 1 FROM users WHERE id=?", (alice,)).fetchone())
                self.assertIsNotNone(db.execute(
                    "SELECT 1 FROM course_catalogs WHERE name='Khóa của Bob'").fetchone())
                backup = db.execute("""SELECT label, payload_json FROM deletion_backups
                    WHERE label='user-bob'""").fetchone()
                self.assertIn("Khóa của Bob", backup["payload_json"])
                self.assertIn("Kế hoạch Bob", backup["payload_json"])

    def test_remote_databases_are_not_migrated_automatically(self):
        self.assertTrue(is_remote_database("libsql://db.turso.io"))
        self.assertTrue(is_remote_database("https://db.turso.io"))
        self.assertFalse(is_remote_database("C:/tmp/target.db"))
        self.assertFalse(is_remote_database(""))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            create_app({"DATABASE": str(path), "AUTO_MIGRATE_DATABASE": False})
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE name='users'").fetchone()[0], 0)

    def test_passwordless_admin_is_claimed_even_without_migrations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "journal.sqlite3")
            create_app({"TESTING": True, "DATABASE": path, "APP_USERNAME": "admin",
                        "APP_PASSWORD": ""})
            app = create_app({"TESTING": True, "AUTH_DISABLED": False, "DATABASE": path,
                              "APP_PASSWORD": "admin-password",
                              "AUTO_MIGRATE_DATABASE": False})
            client = app.test_client()
            self.assertEqual(self.login(client, "admin", "admin-password").status_code, 302)

    def test_user_can_change_own_password(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory)
            client = app.test_client()
            self.login(client, "admin", "admin-password")
            client.post("/account/password", data={
                "current_password": "wrong-password", "new_password": "new-password",
                "confirm_password": "new-password"})
            client.post("/account/password", data={
                "current_password": "admin-password", "new_password": "new-password",
                "confirm_password": "new-password"})
            client.post("/logout")
            self.assertEqual(self.login(client, "admin", "admin-password").status_code, 200)
            self.assertEqual(self.login(client, "admin", "new-password").status_code, 302)


if __name__ == "__main__":
    unittest.main()
