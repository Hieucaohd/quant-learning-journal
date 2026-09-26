import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from app import create_app
from app.ai_kit import _ranges
from app.db import get_db


class AiKitTest(unittest.TestCase):
    def test_ranges_are_compressed(self):
        self.assertEqual(_ranges([5, 1, 2, 3, 7, 8]), "1–3, 5, 7–8")
        self.assertEqual(_ranges([4]), "4")
        self.assertEqual(_ranges([]), "")

    def test_kit_contains_prompt_schema_and_only_the_users_courses(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({
                "TESTING": True, "AUTH_DISABLED": False,
                "DATABASE": str(Path(directory) / "journal.sqlite3"),
                "APP_USERNAME": "admin", "APP_PASSWORD": "admin-password",
            })
            client = app.test_client()
            client.post("/login", data={"username": "admin", "password": "admin-password"})
            client.post("/admin/users", data={
                "username": "bob", "password": "bob-password", "role": "user"})
            client.post("/courses", data={"name": "Giải tích", "estimated_hours": "0"})
            with app.app_context():
                db = get_db()
                course_id = db.execute("SELECT id FROM courses WHERE owner_user_id=1").fetchone()[0]
                for number, status in ((1, "Hoàn thành"), (2, "Hoàn thành"), (3, "Chưa bắt đầu"),
                                       (4, "Chưa bắt đầu"), (6, "Đang học")):
                    db.execute("""INSERT INTO lectures (course_id, lecture_number, title, status)
                        VALUES (?, ?, ?, ?)""", (course_id, number, f"Bài {number}", status))
                bob = db.execute("SELECT id FROM users WHERE username='bob'").fetchone()[0]
                db.execute("""INSERT INTO courses (name, display_name, owner_user_id)
                    VALUES ('Riêng của Bob', 'Riêng của Bob', ?)""", (bob,))
                db.commit()

            page = client.get("/import-plan").get_data(as_text=True)
            self.assertIn("/import-plan/ai-kit.zip", page)
            self.assertIn("Sao chép prompt", page)

            response = client.get("/import-plan/ai-kit.zip")
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response.headers["Content-Disposition"])
            with ZipFile(BytesIO(response.data)) as archive:
                files = {Path(name).name: archive.read(name).decode("utf-8")
                         for name in archive.namelist()}
            self.assertEqual(set(files), {"HUONG_DAN.md", "PROMPT.md", "plan_format.schema.json",
                                          "sample_plan.json", "current_courses.json"})
            prompt = files["PROMPT.md"]
            self.assertIn("**Giải tích**", prompt)
            self.assertIn("cần tạo phần việc: bài 3–4, 6", prompt)
            self.assertIn("đã hoàn thành (bỏ qua): bài 1–2", prompt)
            self.assertIn("Sau khi nhận JSON", prompt)
            self.assertNotIn("Riêng của Bob", prompt)
            data = json.loads(files["current_courses.json"])
            self.assertEqual([course["name"] for course in data["my_courses"]], ["Giải tích"])
            self.assertEqual(json.loads(files["plan_format.schema.json"])["type"], "object")


if __name__ == "__main__":
    unittest.main()
