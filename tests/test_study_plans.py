import tempfile
import unittest
from datetime import date
from pathlib import Path

from app import create_app
from app.db import get_db


class StudyPlanTest(unittest.TestCase):
    def test_catalog_active_plans_filters_and_shared_course(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True,
                              "DATABASE": str(Path(directory) / "journal.sqlite3")})
            with app.app_context():
                db = get_db()
                for name in ("Khóa A", "Khóa B", "Khóa dự định"):
                    db.execute("INSERT INTO courses (name) VALUES (?)", (name,))
                for course_id in (1, 2, 3):
                    db.execute("""INSERT INTO lectures
                        (course_id, lecture_number, remaining_hours)
                        VALUES (?, 1, 4)""", (course_id,))
                db.commit()
            client = app.test_client()
            self.assertEqual(client.post("/plans", data={
                "name": "Kế hoạch B", "priority": "2",
            }).status_code, 302)
            self.assertEqual(client.post("/plans/1/courses", data={"course_id": "1"}).status_code,
                             302)
            self.assertEqual(client.post("/plans/2/courses", data={"course_id": "2"}).status_code,
                             302)
            day = date.today().isoformat()
            with app.app_context():
                rows = get_db().execute("""SELECT course_id, SUM(hours) FROM schedule_allocations
                    WHERE study_date=? GROUP BY course_id ORDER BY course_id""", (day,)).fetchall()
                self.assertEqual([row[0] for row in rows], [1, 2])
                self.assertEqual(round(sum(row[1] for row in rows), 2),
                                 8 if date.today().weekday() >= 5 else 4)
                self.assertIsNone(get_db().execute(
                    "SELECT deadline FROM lectures WHERE course_id=3").fetchone()[0])
            first = client.get(f"/schedule?date={day}&filter=1&plan_id=1")
            self.assertEqual(first.status_code, 200)
            self.assertIn("Khóa A", first.get_data(as_text=True))
            self.assertNotIn("Khóa B", first.get_data(as_text=True))
            both = client.get(f"/schedule?date={day}&filter=1&plan_id=1&plan_id=2")
            self.assertEqual(both.status_code, 200)
            self.assertIn("Khóa A", both.get_data(as_text=True))
            self.assertIn("Khóa B", both.get_data(as_text=True))
            self.assertIn('calendar-plans', both.get_data(as_text=True))
            self.assertIn('Kế hoạch B', both.get_data(as_text=True))
            self.assertEqual(client.post("/plans/2/courses", data={"course_id": "1"}).status_code,
                             302)
            with app.app_context():
                self.assertEqual(get_db().execute("""SELECT SUM(hours) FROM schedule_allocations
                    WHERE course_id=1 AND study_date=?""", (day,)).fetchone()[0],
                                 rows[0][1])
            self.assertEqual(client.post("/plans/2", data={
                "name": "Kế hoạch B", "description": "", "status": "Tạm dừng", "priority": "2",
            }).status_code, 302)
            with app.app_context():
                self.assertEqual(get_db().execute("""SELECT COUNT(*) FROM schedule_allocations
                    WHERE course_id=2 AND study_date>=?""", (day,)).fetchone()[0], 0)
                self.assertGreater(get_db().execute("""SELECT COUNT(*) FROM schedule_allocations
                    WHERE course_id=1 AND study_date>=?""", (day,)).fetchone()[0], 0)
                self.assertEqual(get_db().execute("SELECT COUNT(*) FROM courses").fetchone()[0], 3)
            self.assertEqual(client.post("/plans/2/delete", data={
                "name": "Kế hoạch B",
            }).status_code, 302)
            with app.app_context():
                self.assertEqual(get_db().execute("SELECT COUNT(*) FROM courses").fetchone()[0], 3)
                self.assertEqual(get_db().execute(
                    "SELECT COUNT(*) FROM study_plans").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
