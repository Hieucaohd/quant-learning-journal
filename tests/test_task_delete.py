import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import create_app
from app.db import get_db


class TaskDeleteTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.backup_dir = root / "backups"
        self.app = create_app({"TESTING": True, "DATABASE": str(root / "journal.sqlite3"),
                               "BACKUP_DIR": str(self.backup_dir)})
        self.client = self.app.test_client()
        with self.app.app_context():
            db = get_db()
            db.execute("INSERT INTO courses (name, display_name) VALUES ('Giải tích', 'Giải tích')")
            db.execute("INSERT INTO plan_courses (plan_id, course_id, position) VALUES (1, 1, 1)")
            self.lecture = db.execute("""INSERT INTO lectures
                (course_id, lecture_number, title, estimated_hours, remaining_hours)
                VALUES (1, 1, 'Đạo hàm', 0, 0)""").lastrowid
            self.video, self.exercises = (db.execute("""INSERT INTO lecture_tasks
                (lecture_id, position, kind, title, estimated_hours, remaining_hours)
                VALUES (?, ?, ?, ?, ?, ?)""", (self.lecture, position, kind, title, hours, hours)
                ).lastrowid for position, kind, title, hours in (
                    (1, "video", "Xem bài giảng", 2), (2, "exercises", "Newton's method", 3)))
            db.commit()
        self.client.get("/schedule")  # builds the initial schedule

    def tearDown(self):
        self.directory.cleanup()

    def query(self, sql, *params):
        with self.app.app_context():
            return get_db().execute(sql, params).fetchall()

    def lecture_row(self):
        return self.query("""SELECT status, estimated_hours, remaining_hours, completed_at
            FROM lectures WHERE id=?""", self.lecture)[0]

    def test_button_is_shown_with_a_safe_confirmation(self):
        page = self.client.get("/courses/1").get_data(as_text=True)
        self.assertIn(f"/tasks/{self.exercises}/delete", page)
        self.assertIn("Xóa phần việc", page)
        # The apostrophe sits in a data attribute, never inside inline JS.
        self.assertIn('data-confirm="Xóa phần việc “Newton&#39;s method” của Bài 1?', page)
        self.assertIn("onsubmit=\"return confirm(this.dataset.confirm)\"", page)

    def test_delete_updates_lecture_hours_schedule_and_history(self):
        self.assertTrue(self.query("SELECT 1 FROM schedule_allocations WHERE task_id=?", self.video))
        response = self.client.post(f"/tasks/{self.video}/delete", data={"confirmation": "delete"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.query("SELECT 1 FROM lecture_tasks WHERE id=?", self.video))
        self.assertFalse(self.query("SELECT 1 FROM schedule_allocations WHERE task_id=?", self.video))
        self.assertEqual(self.query("SELECT ROUND(SUM(hours), 2) FROM schedule_allocations")[0][0], 3)
        self.assertEqual(tuple(self.lecture_row())[:3], ("Chưa bắt đầu", 3, 3))
        self.assertTrue(self.query("""SELECT 1 FROM schedule_events
            WHERE kind='xóa phần việc' AND reason LIKE '%Xem bài giảng%'"""))
        self.assertEqual(len(list(self.backup_dir.glob("*task-*.sqlite3"))), 1)

    def test_delete_requires_confirmation(self):
        self.client.post(f"/tasks/{self.video}/delete", data={})
        self.assertTrue(self.query("SELECT 1 FROM lecture_tasks WHERE id=?", self.video))

    def test_deleting_the_last_open_part_completes_the_lecture(self):
        done_day = (date.today() - timedelta(days=1)).isoformat()
        self.client.post(f"/tasks/{self.video}/complete", data={
            "completed_at": done_day, "return_to_course": "1"})
        self.client.post(f"/tasks/{self.exercises}/delete", data={"confirmation": "delete"})
        status, estimated, remaining, completed_at = self.lecture_row()
        self.assertEqual((status, estimated, remaining, completed_at),
                         ("Hoàn thành", 2, 0, done_day))

    def test_deleting_every_part_keeps_a_finished_lecture_finished(self):
        done_day = date.today().isoformat()
        for task_id in (self.video, self.exercises):
            self.client.post(f"/tasks/{task_id}/complete", data={
                "completed_at": done_day, "return_to_course": "1"})
        for task_id in (self.video, self.exercises):
            self.client.post(f"/tasks/{task_id}/delete", data={"confirmation": "delete"})
        self.assertFalse(self.query("SELECT 1 FROM lecture_tasks WHERE lecture_id=?", self.lecture))
        status, estimated, remaining, completed_at = self.lecture_row()
        self.assertEqual((status, completed_at), ("Hoàn thành", done_day))
        self.assertEqual((estimated, remaining), (0, 0))

    def test_other_users_cannot_delete_a_part(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True, "AUTH_DISABLED": False,
                              "DATABASE": str(Path(directory) / "journal.sqlite3"),
                              "BACKUP_DIR": str(Path(directory) / "backups"),
                              "APP_USERNAME": "admin", "APP_PASSWORD": "admin-password"})
            client = app.test_client()
            with app.app_context():
                db = get_db()
                db.execute("INSERT INTO courses (name, display_name) VALUES ('Riêng', 'Riêng')")
                lecture = db.execute("""INSERT INTO lectures (course_id, lecture_number, title)
                    VALUES (1, 1, 'Bài')""").lastrowid
                task = db.execute("""INSERT INTO lecture_tasks
                    (lecture_id, position, kind, title, estimated_hours, remaining_hours)
                    VALUES (?, 1, 'video', 'Của admin', 1, 1)""", (lecture,)).lastrowid
                db.commit()
            client.post("/login", data={"username": "admin", "password": "admin-password"})
            client.post("/admin/users", data={"username": "bob", "password": "bob-password"})
            client.post("/logout")
            client.post("/login", data={"username": "bob", "password": "bob-password"})
            response = client.post(f"/tasks/{task}/delete", data={"confirmation": "delete"})
            self.assertEqual(response.status_code, 404)
            with app.app_context():
                self.assertTrue(get_db().execute(
                    "SELECT 1 FROM lecture_tasks WHERE id=?", (task,)).fetchone())


if __name__ == "__main__":
    unittest.main()
