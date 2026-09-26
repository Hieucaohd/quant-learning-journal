import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

from app import create_app
from app.db import get_db


class LectureDeleteTest(unittest.TestCase):
    def test_delete_lesson_backs_up_cascades_renumbers_and_replans(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backup_dir = root / "backups"
            app = create_app({
                "TESTING": True,
                "DATABASE": str(root / "journal.sqlite3"),
                "BACKUP_DIR": str(backup_dir),
            })
            with app.app_context():
                db = get_db()
                db.execute("INSERT INTO courses (name) VALUES ('Khóa xóa bài')")
                db.execute("INSERT INTO plan_courses (plan_id, course_id, position) VALUES (1, 1, 1)")
                db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, title, status, estimated_hours,
                     remaining_hours, completed_at)
                    VALUES (1, 11, 'Bài 11', 'Hoàn thành', 1, 0, ?)""",
                    (date.today().isoformat(),))
                deleted_id = db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, title, estimated_hours, remaining_hours)
                    VALUES (1, 12, 'Bài cần xóa', 2, 2)""").lastrowid
                shifted_id = db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, title, estimated_hours, remaining_hours)
                    VALUES (1, 13, 'Bài 13', 1, 1)""").lastrowid
                custom_id = db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, title, estimated_hours, remaining_hours)
                    VALUES (1, 14, 'Tên tùy chỉnh', 1, 1)""").lastrowid
                deleted_task = db.execute("""INSERT INTO lecture_tasks
                    (lecture_id, position, kind, title, estimated_hours, remaining_hours)
                    VALUES (?, 1, 'review', 'Ôn phần sẽ xóa', 2, 2)""",
                    (deleted_id,)).lastrowid
                kept_task = db.execute("""INSERT INTO lecture_tasks
                    (lecture_id, position, kind, title, estimated_hours, remaining_hours)
                    VALUES (?, 1, 'video', 'Phần việc giữ lại', 1, 1)""",
                    (shifted_id,)).lastrowid
                db.commit()

            client = app.test_client()
            client.get("/schedule")
            page = client.get("/courses/1").get_data(as_text=True)
            self.assertIn("Xóa bài học", page)
            self.assertIn(f'/lectures/{deleted_id}/delete', page)

            response = client.post(f"/lectures/{deleted_id}/delete", data={})
            self.assertEqual(response.status_code, 302)
            with app.app_context():
                self.assertIsNotNone(get_db().execute(
                    "SELECT 1 FROM lectures WHERE id=?", (deleted_id,)).fetchone())
            self.assertFalse(backup_dir.exists())

            response = client.post(f"/lectures/{deleted_id}/delete",
                                   data={"confirmation": "delete"},
                                   follow_redirects=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn("Đã xóa Bài 12 · Bài cần xóa", response.get_data(as_text=True))

            backups = list(backup_dir.glob("journal-before-lecture-*.sqlite3"))
            self.assertEqual(len(backups), 1)
            with closing(sqlite3.connect(backups[0])) as backup:
                self.assertEqual(backup.execute(
                    "SELECT COUNT(*) FROM lectures WHERE course_id=1").fetchone()[0], 4)
                self.assertEqual(backup.execute(
                    "SELECT COUNT(*) FROM lecture_tasks WHERE id=?", (deleted_task,)).fetchone()[0], 1)

            with app.app_context():
                db = get_db()
                lessons = db.execute("""SELECT id, lecture_number, title FROM lectures
                    WHERE course_id=1 ORDER BY lecture_number""").fetchall()
                self.assertEqual([(row["lecture_number"], row["title"]) for row in lessons],
                                 [(11, "Bài 11"), (12, "Bài 12"), (13, "Tên tùy chỉnh")])
                self.assertEqual(db.execute(
                    "SELECT lecture_number FROM lectures WHERE id=?", (shifted_id,)).fetchone()[0], 12)
                self.assertEqual(db.execute(
                    "SELECT lecture_number FROM lectures WHERE id=?", (custom_id,)).fetchone()[0], 13)
                self.assertIsNone(db.execute(
                    "SELECT 1 FROM lecture_tasks WHERE id=?", (deleted_task,)).fetchone())
                self.assertEqual(db.execute(
                    "SELECT lecture_id FROM lecture_tasks WHERE id=?", (kept_task,)).fetchone()[0],
                                 shifted_id)
                self.assertIsNone(db.execute(
                    "SELECT 1 FROM schedule_allocations WHERE lecture_id=?", (deleted_id,)).fetchone())
                event = db.execute("""SELECT lecture_id, old_deadline, new_deadline, kind, reason
                    FROM schedule_events WHERE kind='xóa bài học'""").fetchone()
                self.assertIsNone(event["lecture_id"])
                self.assertIsNone(event["new_deadline"])
                self.assertIn("1 phần việc", event["reason"])


if __name__ == "__main__":
    unittest.main()
