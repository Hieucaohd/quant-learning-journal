import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app import create_app
from app.db import get_db


class CourseDeleteTest(unittest.TestCase):
    def test_confirmation_backup_and_related_data(self):
        with tempfile.TemporaryDirectory() as directory:
            backup_dir = Path(directory) / "backups"
            app = create_app({"TESTING": True,
                              "DATABASE": str(Path(directory) / "journal.sqlite3"),
                              "BACKUP_DIR": str(backup_dir)})
            with app.app_context():
                db = get_db()
                db.execute("INSERT INTO courses (name) VALUES ('Khóa cần xóa')")
                db.execute("INSERT INTO courses (name) VALUES ('Khóa giữ lại')")
                db.execute("INSERT INTO plan_courses (plan_id, course_id, position) VALUES (1, 1, 1)")
                db.execute("INSERT INTO plan_courses (plan_id, course_id, position) VALUES (1, 2, 2)")
                db.execute("INSERT INTO lectures (course_id, lecture_number) VALUES (1, 1)")
                db.execute("INSERT INTO lectures (course_id, lecture_number) VALUES (2, 1)")
                db.execute("""INSERT INTO lecture_tasks
                    (lecture_id, position, kind, title, estimated_hours, remaining_hours)
                    VALUES (1, 1, 'video', 'Xem bài', 1, 1)""")
                db.execute("""INSERT INTO journals (date, course_id, hours)
                    VALUES ('2026-09-20', 1, 2)""")
                db.commit()
            client = app.test_client()
            self.assertEqual(client.get("/schedule").status_code, 200)
            response = client.get("/courses/1/delete")
            self.assertEqual(response.status_code, 200)
            page = response.get_data(as_text=True)
            self.assertIn("1 mục nhật ký", page)
            self.assertIn("1 phần việc", page)
            response = client.post("/courses/1/delete", data={"course_name": "Sai tên"})
            self.assertEqual(response.status_code, 200)
            with app.app_context():
                self.assertEqual(get_db().execute("SELECT COUNT(*) FROM courses").fetchone()[0], 2)
            self.assertFalse(backup_dir.exists())
            response = client.post("/courses/1/delete", data={"course_name": "Khóa cần xóa"})
            self.assertEqual(response.status_code, 302)
            backups = list(backup_dir.glob("*.sqlite3"))
            self.assertEqual(len(backups), 1)
            with closing(sqlite3.connect(backups[0])) as backup:
                self.assertEqual(backup.execute("SELECT COUNT(*) FROM courses").fetchone()[0], 2)
            with app.app_context():
                db = get_db()
                self.assertEqual(db.execute("SELECT COUNT(*) FROM courses").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT name FROM courses").fetchone()[0], "Khóa giữ lại")
                self.assertEqual(db.execute("SELECT COUNT(*) FROM journals").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM lecture_tasks").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM lectures").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM schedule_allocations WHERE course_id=2").fetchone()[0], 0)
            self.assertEqual(client.get("/courses/1").status_code, 404)


if __name__ == "__main__":
    unittest.main()
