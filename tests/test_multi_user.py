import tempfile
import unittest
from pathlib import Path

from app import create_app
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


if __name__ == "__main__":
    unittest.main()
