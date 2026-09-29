import json
import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.db import get_db


class CourseTransferTest(unittest.TestCase):
    def make_app(self, root):
        app = create_app({
            "TESTING": True,
            "DATABASE": str(root / "journal.sqlite3"),
            "BACKUP_DIR": str(root / "backups"),
        })
        with app.app_context():
            db = get_db()
            db.execute("UPDATE study_plans SET name='Kế hoạch A' WHERE id=1")
            db.execute("""INSERT INTO study_plans
                (name, status, priority, owner_user_id)
                VALUES ('Kế hoạch B', 'Đang hoạt động', 200, 1)""")
            db.execute("""INSERT INTO courses
                (name, display_name, description, start_date, status, owner_user_id)
                VALUES ('Khóa gốc', 'Khóa gốc', 'Mô tả', '2026-09-01', 'Đang học', 1)""")
            db.execute("INSERT INTO plan_courses VALUES (1, 1, 1)")
            db.execute("INSERT INTO plan_courses VALUES (2, 1, 1)")
            finished = db.execute("""INSERT INTO lectures
                (course_id, lecture_number, title, status, estimated_hours, remaining_hours,
                 deadline, completed_at, completed_on_time)
                VALUES (1, 1, 'Bài đã xong', 'Hoàn thành', 2, 0,
                        '2026-09-20', '2026-09-19', 1)""").lastrowid
            db.execute("""INSERT INTO lecture_tasks
                (lecture_id, position, kind, title, estimated_hours, remaining_hours,
                 status, deadline, completed_at, completed_on_time)
                VALUES (?, 1, 'video', 'Xem video', 2, 0, 'Hoàn thành',
                        '2026-09-20', '2026-09-19', 1)""", (finished,))
            current = db.execute("""INSERT INTO lectures
                (course_id, lecture_number, title, status, estimated_hours, remaining_hours)
                VALUES (1, 2, 'Bài đang học', 'Đang học', 2, 2)""").lastrowid
            db.execute("""INSERT INTO lecture_tasks
                (lecture_id, position, kind, title, estimated_hours, remaining_hours, status)
                VALUES (?, 1, 'exercises', 'Làm bài tập', 2, 2, 'Đang học')""", (current,))
            db.execute("""INSERT INTO journals
                (date, course_id, topic, hours, user_id)
                VALUES ('2026-09-19', 1, 'Ôn bài', 1, 1)""")
            db.commit()
        return app

    def revised_snapshot(self, client):
        response = client.get("/plans/1/courses/1/export.json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        snapshot = json.loads(response.get_data(as_text=True))
        self.assertEqual(snapshot["schema_version"], 2)
        self.assertEqual(snapshot["export_type"], "course_plan_snapshot")
        self.assertEqual(snapshot["course"]["lectures"][0]["status"], "Hoàn thành")
        self.assertEqual(snapshot["course"]["lectures"][0]["tasks"][0]["completed_at"],
                         "2026-09-19")
        self.assertEqual(snapshot["history"]["journals"][0]["topic"], "Ôn bài")
        self.assertIn("ai_instructions", snapshot)

        snapshot["course"]["name"] = "Khóa đã điều chỉnh"
        second = snapshot["course"]["lectures"][1]
        second["title"] = "Bài đã điều chỉnh"
        second["tasks"][0].update({
            "status": "Hoàn thành", "remaining_hours": 0,
            "completed_at": "2026-09-25", "completed_on_time": True,
        })
        snapshot["course"]["lectures"].append({
            "number": 3, "title": "Bài bổ sung", "content": "Ôn phần còn yếu",
            "status": "Chưa bắt đầu", "estimated_hours": 1.5,
            "remaining_hours": 1.5, "understanding_score": None, "notes": "",
            "deadline": None, "manual_deadline": None, "completed_at": None,
            "completed_on_time": None,
            "tasks": [{
                "position": 1, "type": "review", "title": "Ôn tập", "content": "",
                "estimated_hours": 1.5, "remaining_hours": 1.5,
                "status": "Chưa bắt đầu", "deadline": None, "manual_deadline": None,
                "completed_at": None, "completed_on_time": None,
            }],
        })
        return snapshot

    def test_export_preview_and_overwrite_only_selected_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = self.make_app(root)
            client = app.test_client()
            snapshot = self.revised_snapshot(client)
            raw = json.dumps(snapshot, ensure_ascii=False)

            preview = client.post("/import-plan", data={
                "mode": "preview", "import_type": "overwrite_course",
                "plan_id": "1", "payload": raw,
            })
            self.assertEqual(preview.status_code, 200)
            page = preview.get_data(as_text=True)
            self.assertIn("Kế hoạch A", page)
            self.assertIn("Kế hoạch B", page)
            self.assertIn("Xác nhận ghi đè và lập lại lịch", page)
            with app.app_context():
                self.assertEqual(get_db().execute("SELECT COUNT(*) FROM courses").fetchone()[0], 1)

            rejected = client.post("/import-plan", data={
                "mode": "confirm", "import_type": "overwrite_course", "plan_id": "1",
                "payload": raw,
            })
            self.assertEqual(rejected.status_code, 200)
            with app.app_context():
                self.assertEqual(get_db().execute("SELECT COUNT(*) FROM courses").fetchone()[0], 1)
            self.assertFalse((root / "backups").exists())

            result = client.post("/import-plan", data={
                "mode": "confirm", "import_type": "overwrite_course", "plan_id": "1",
                "target_plan_id": "1", "payload": raw, "source_name": "ai-revised.json",
            })
            self.assertEqual(result.status_code, 302)
            with app.app_context():
                db = get_db()
                plan_a = db.execute("SELECT course_id FROM plan_courses WHERE plan_id=1").fetchone()[0]
                plan_b = db.execute("SELECT course_id FROM plan_courses WHERE plan_id=2").fetchone()[0]
                self.assertNotEqual(plan_a, plan_b)
                self.assertEqual(plan_b, 1)
                self.assertEqual(db.execute("SELECT display_name FROM courses WHERE id=?",
                                            (plan_a,)).fetchone()[0], "Khóa đã điều chỉnh")
                self.assertEqual(db.execute("SELECT title FROM lectures WHERE course_id=? AND lecture_number=2",
                                            (plan_a,)).fetchone()[0], "Bài đã điều chỉnh")
                imported_task = db.execute("""SELECT t.status, t.remaining_hours, t.completed_at
                    FROM lecture_tasks t JOIN lectures l ON l.id=t.lecture_id
                    WHERE l.course_id=? AND l.lecture_number=2""", (plan_a,)).fetchone()
                self.assertEqual(tuple(imported_task), ("Hoàn thành", 0, "2026-09-25"))
                old_task = db.execute("""SELECT t.status FROM lecture_tasks t
                    JOIN lectures l ON l.id=t.lecture_id
                    WHERE l.course_id=1 AND l.lecture_number=2""").fetchone()[0]
                self.assertEqual(old_task, "Đang học")
                self.assertEqual(db.execute("SELECT COUNT(*) FROM journals WHERE course_id=1").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM plan_imports").fetchone()[0], 1)
                self.assertGreater(db.execute("""SELECT COUNT(*) FROM schedule_allocations
                    WHERE course_id=?""", (plan_a,)).fetchone()[0], 0)
                self.assertTrue(db.execute("""SELECT 1 FROM schedule_events
                    WHERE course_id=? AND kind='ghi đè'""", (plan_a,)).fetchone())
            self.assertEqual(len(list((root / "backups").glob("*.sqlite3"))), 1)

    def test_selecting_all_plans_replaces_shared_course_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = self.make_app(root)
            client = app.test_client()
            raw = json.dumps(self.revised_snapshot(client), ensure_ascii=False)
            result = client.post("/import-plan", data={
                "mode": "confirm", "import_type": "overwrite_course", "plan_id": "1",
                "target_plan_id": ["1", "2"], "payload": raw,
            })
            self.assertEqual(result.status_code, 302)
            with app.app_context():
                db = get_db()
                self.assertEqual(db.execute("SELECT COUNT(*) FROM courses").fetchone()[0], 1)
                ids = [row[0] for row in db.execute(
                    "SELECT course_id FROM plan_courses ORDER BY plan_id")]
                self.assertEqual(ids, [1, 1])
                self.assertEqual(db.execute("SELECT display_name FROM courses WHERE id=1").fetchone()[0],
                                 "Khóa đã điều chỉnh")
                self.assertEqual(db.execute("SELECT COUNT(*) FROM journals WHERE course_id=1").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
