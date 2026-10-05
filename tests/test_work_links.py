import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.db import get_db


class WorkLinksTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = create_app({"TESTING": True, "AUTH_DISABLED": False,
                               "DATABASE": str(Path(self.directory.name) / "journal.sqlite3"),
                               "APP_USERNAME": "admin", "APP_PASSWORD": "admin-password"})
        self.client = self.app.test_client()
        self.login("admin")
        self.client.post("/admin/users", data={"username": "bob", "password": "bob-password",
                                               "role": "user"})
        self.client.post("/admin/users", data={"username": "alice", "password": "alice-password",
                                               "role": "user"})
        self.login("bob")
        self.client.post("/courses", data={"name": "Calculus", "estimated_hours": "0"})
        self.client.post("/plans", data={"name": "My plan", "priority": "1"})
        with self.app.app_context():
            db = get_db()
            self.course = db.execute("SELECT id FROM courses WHERE name='Calculus'").fetchone()[0]
            self.plan = db.execute("SELECT id FROM study_plans WHERE name='My plan'").fetchone()[0]
            self.lecture = db.execute("""INSERT INTO lectures
                (course_id, lecture_number, title, estimated_hours, remaining_hours)
                VALUES (?, 1, 'Integrals', 4.5, 4.5)""", (self.course,)).lastrowid
            self.task = db.execute("""INSERT INTO lecture_tasks
                (lecture_id, kind, title, estimated_hours, remaining_hours, position)
                VALUES (?, 'video', 'Watch integrals', 4.5, 4.5, 1)""",
                (self.lecture,)).lastrowid
            db.execute("""UPDATE user_capacity_profiles SET hours_json='[4,4,4,4,4,4,4]'
                WHERE user_id=(SELECT id FROM users WHERE username='bob')""")
            db.commit()
        self.client.post(f"/plans/{self.plan}/courses", data={"course_id": self.course})
        self.path = f"/plans/{self.plan}/courses/{self.course}"

    def login(self, username):
        self.client.post("/logout")
        self.client.post("/login", data={"username": username, "password": f"{username}-password"})

    def test_calendar_links_every_split_to_same_work_and_keeps_plan_on_save(self):
        with self.app.app_context():
            days = get_db().execute("SELECT study_date FROM schedule_allocations WHERE task_id=?",
                                   (self.task,)).fetchall()
        self.assertEqual(len(days), 2)
        for day in days:
            page = self.client.get("/schedule", query_string={"date": day[0]}).get_data(as_text=True)
            self.assertIn(f'href="{self.path}#task-{self.task}"', page)
        page = self.client.get(self.path).get_data(as_text=True)
        self.assertIn(f'id="lecture-{self.lecture}"', page)
        self.assertIn(f'id="task-{self.task}"', page)
        self.assertIn('work_links.js', page)
        self.assertIn(f'data-plan-id="{self.plan}"', page)
        self.assertIn(f'<form action="/courses/{self.course}" method="post"', page)
        saved = self.client.post(f"/tasks/{self.task}", data={
            "title": "Watch integrals again", "kind": "video", "estimated_hours": "4.5",
            "remaining_hours": "4.5", "content": "New content"},
            headers={"X-Plan-Id": str(self.plan)}, follow_redirects=True)
        self.assertEqual(saved.status_code, 200)
        self.assertIn(f'data-plan-id="{self.plan}"', saved.get_data(as_text=True))

    def test_shared_work_is_read_only_and_membership_is_checked(self):
        self.client.post(f"/plans/{self.plan}/share", data={"username": "alice"})
        self.login("alice")
        with self.app.app_context():
            day = get_db().execute("SELECT study_date FROM schedule_allocations WHERE task_id=?",
                                   (self.task,)).fetchone()[0]
        calendar = self.client.get("/schedule", query_string={"date": day}).get_data(as_text=True)
        self.assertIn(f'href="{self.path}#task-{self.task}"', calendar)
        page = self.client.get(self.path)
        self.assertEqual(page.status_code, 200)
        text = page.get_data(as_text=True)
        self.assertIn(f'id="task-{self.task}"', text)
        self.assertIn("CHỈ XEM", text)
        self.assertNotIn(f'action="/tasks/{self.task}"', text)
        self.assertEqual(self.client.get(f"/courses/{self.course}").status_code, 404)
        self.assertEqual(self.client.get(f"/plans/{self.plan}/courses/999999").status_code, 404)
        self.assertEqual(self.client.post(f"/tasks/{self.task}/delete").status_code, 404)
        self.login("bob")
        with self.app.app_context():
            alice = get_db().execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
        self.client.post(f"/plans/{self.plan}/shares/{alice}/remove")
        self.login("alice")
        self.assertEqual(self.client.get(self.path).status_code, 403)
        self.login("admin")
        self.assertEqual(self.client.get(self.path).status_code, 200)

