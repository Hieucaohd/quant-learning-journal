import json
import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.db import get_db, seed_demo
from app.reports import build_export


class VietnameseTextTest(unittest.TestCase):
    def test_existing_data_and_export(self):
        with tempfile.TemporaryDirectory() as temp:
            config = {"TESTING": True, "DATABASE": str(Path(temp) / "journal.sqlite3"),
                      "EXPORT_DIR": str(Path(temp) / "exports")}
            app = create_app(config)
            with app.app_context():
                seed_demo()
                seed_demo()
                db = get_db()
                self.assertEqual(db.execute("SELECT COUNT(*) FROM courses").fetchone()[0], 5)
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM lectures WHERE status='Hoàn thành'"
                ).fetchone()[0], 12)
                db.execute("UPDATE courses SET status='Learning' WHERE name='MIT 18.01'")
                db.execute("UPDATE journals SET difficulty='Medium', topic='Review lectures 1–12'")
                db.commit()

            app = create_app(config)
            with app.app_context():
                db = get_db()
                self.assertEqual(db.execute(
                    "SELECT status FROM courses WHERE name='MIT 18.01'"
                ).fetchone()[0], "Đang học")
                journal = db.execute("SELECT difficulty, topic FROM journals").fetchone()
                self.assertEqual(tuple(journal), ("Trung bình", "Ôn tập bài 1–12"))
                files = build_export(app.config["EXPORT_DIR"])
                self.assertIn("# Tiến độ kế hoạch", files["learning_summary.md"])
                self.assertEqual(json.loads(files["learning_data.json"])["journals"][0]["difficulty"],
                                 "Trung bình")

            client = app.test_client()
            for url in ("/", "/courses", "/courses/1", "/journal", "/journal/1",
                        "/reviews", "/reviews?period=month", "/schedule", "/import-plan"):
                response = client.get(url)
                self.assertEqual(response.status_code, 200, url)
                self.assertIn('lang="vi"', response.get_data(as_text=True))
            home = client.get("/").get_data(as_text=True)
            self.assertIn("Xuất dữ liệu", home)
            self.assertNotIn("Xuất cho ChatGPT", home)
            course_page = client.get("/courses/1").get_data(as_text=True)
            self.assertNotIn("Deadline:", course_page)
            self.assertIn("Hạn dự kiến:", course_page)
            import_page = client.get("/import-plan").get_data(as_text=True)
            self.assertIn("Chọn tệp JSON", import_page)
            self.assertIn('id="plan-file" type="file"', import_page)
            self.assertIn('accept=".json,application/json" hidden', import_page)
            self.assertEqual(client.post("/journal", data={
                "date": "2026-09-20", "course_id": "1", "hours": "4",
                "difficulty": "Khó", "topic": "Tích phân",
            }).status_code, 302)
            self.assertEqual(client.post("/lectures/13", data={
                "title": "Tích phân", "status": "Hoàn thành", "understanding_score": "8",
            }).status_code, 302)
            self.assertEqual(client.post("/export").status_code, 200)


if __name__ == "__main__":
    unittest.main()
