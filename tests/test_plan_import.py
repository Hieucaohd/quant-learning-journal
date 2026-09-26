import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from app import create_app
from app.db import get_db, seed_demo


class PlanImportTest(unittest.TestCase):
    def test_preview_import_schedule_and_task_completion(self):
        sample = (Path(__file__).resolve().parent.parent / "app" / "static" / "sample_plan.json").read_text(
            encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True,
                              "DATABASE": str(Path(directory) / "journal.sqlite3"),
                              "EXPORT_DIR": str(Path(directory) / "exports")})
            with app.app_context():
                seed_demo()
            client = app.test_client()
            response = client.post("/import-plan", data={
                "mode": "preview", "plan_file": (io.BytesIO(sample.encode("utf-8")), "plan.json"),
            }, content_type="multipart/form-data")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Xem trước trước khi nhập", response.get_data(as_text=True))
            with app.app_context():
                self.assertEqual(get_db().execute("SELECT COUNT(*) FROM lecture_tasks").fetchone()[0], 0)
            response = client.post("/import-plan", data={"mode": "confirm", "payload": sample,
                                                         "source_name": "plan.json"})
            self.assertEqual(response.status_code, 302)
            self.assertIn("Xem bài giảng", client.get("/courses/1").get_data(as_text=True))
            self.assertIn("Xem bài giảng", client.get("/schedule").get_data(as_text=True))
            with app.app_context():
                db = get_db()
                parts = db.execute("""SELECT t.* FROM lecture_tasks t JOIN lectures l ON
                    l.id=t.lecture_id WHERE l.lecture_number=13 ORDER BY t.position""").fetchall()
                self.assertEqual(len(parts), 3)
                self.assertEqual(sum(part["remaining_hours"] for part in parts), 3.5)
                self.assertTrue(all(part["deadline"] for part in parts))
                self.assertEqual(db.execute("""SELECT COUNT(*) FROM schedule_allocations
                    WHERE task_id IS NOT NULL""").fetchone()[0], 3)
                first_id = parts[0]["id"]
            response = client.post("/import-plan", data={"mode": "confirm", "payload": sample})
            self.assertEqual(response.status_code, 200)
            with app.app_context():
                self.assertEqual(get_db().execute("SELECT COUNT(*) FROM lecture_tasks").fetchone()[0], 3)
            self.assertEqual(client.post(f"/tasks/{first_id}/complete").status_code, 302)
            with app.app_context():
                db = get_db()
                task = db.execute("SELECT status, remaining_hours FROM lecture_tasks WHERE id=?",
                                  (first_id,)).fetchone()
                self.assertEqual(tuple(task), ("Hoàn thành", 0))
                remaining = db.execute("SELECT remaining_hours FROM lectures WHERE lecture_number=13").fetchone()[0]
                self.assertEqual(remaining, 2.0)
            self.assertEqual(client.post("/export").status_code, 200)
            exported = json.loads((Path(directory) / "exports" / "learning_data.json").read_text(
                encoding="utf-8"))
            self.assertEqual(len(exported["lecture_tasks"]), 3)
            self.assertEqual(len(exported["plan_imports"]), 1)

    def test_invalid_plan_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True,
                              "DATABASE": str(Path(directory) / "journal.sqlite3")})
            client = app.test_client()
            invalid = {"schema_version": 1, "courses": [{"name": "Khóa thử", "lectures": [
                {"number": 1, "title": "Bài thử", "tasks": [
                    {"type": "video", "title": "Xem", "hours": -1}]}]}]}
            response = client.post("/import-plan", data={"mode": "confirm",
                                                         "payload": json.dumps(invalid)})
            self.assertEqual(response.status_code, 200)
            with app.app_context():
                self.assertEqual(get_db().execute("SELECT COUNT(*) FROM courses").fetchone()[0], 0)

    def test_task_miss_keeps_reason_and_moves_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True,
                              "DATABASE": str(Path(directory) / "journal.sqlite3")})
            payload = {"schema_version": 1, "courses": [{"name": "Khóa thử", "lectures": [
                {"number": 1, "title": "Bài thử", "tasks": [
                    {"type": "video", "title": "Xem bài", "hours": 1}]}]}]}
            client = app.test_client()
            self.assertEqual(client.post("/import-plan", data={
                "mode": "confirm", "payload": json.dumps(payload),
            }).status_code, 302)
            self.assertEqual(client.post("/plans/1/courses", data={"course_id": "1"}).status_code,
                             302)
            with app.app_context():
                db = get_db()
                task = db.execute("SELECT id, deadline FROM lecture_tasks").fetchone()
                self.assertEqual(task["deadline"], date.today().isoformat())
                task_id = task["id"]
            self.assertEqual(client.post(f"/tasks/{task_id}/miss", data={
                "reason": "Cần xem lại phần nền tảng",
            }).status_code, 302)
            with app.app_context():
                db = get_db()
                deadline = db.execute("SELECT deadline FROM lecture_tasks WHERE id=?",
                                      (task_id,)).fetchone()[0]
                self.assertGreater(deadline, date.today().isoformat())
                missed = db.execute("SELECT task_id, old_deadline, new_deadline, reason "
                                    "FROM missed_deadlines").fetchone()
                self.assertEqual(missed["task_id"], task_id)
                self.assertEqual(missed["new_deadline"], deadline)
                self.assertIn("Cần xem lại", missed["reason"])


if __name__ == "__main__":
    unittest.main()
