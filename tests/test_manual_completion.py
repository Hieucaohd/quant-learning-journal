import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import create_app
from app.db import get_db
from app.reports import report_data
from app.reports import courses_with_progress


class ManualCompletionTest(unittest.TestCase):
    def test_backdated_task_completion_and_date_correction_replan(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True,
                              "DATABASE": str(Path(directory) / "journal.sqlite3")})
            with app.app_context():
                db = get_db()
                db.execute("INSERT INTO courses (name) VALUES ('Khóa thử')")
                db.execute("INSERT INTO plan_courses (plan_id, course_id, position) VALUES (1, 1, 1)")
                db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, remaining_hours) VALUES (1, 1, 8)""")
                db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, remaining_hours) VALUES (1, 2, 4)""")
                for position in (1, 2):
                    db.execute("""INSERT INTO lecture_tasks
                        (lecture_id, position, kind, title, estimated_hours, remaining_hours)
                        VALUES (1, ?, 'video', ?, 4, 4)""", (position, f"Phần {position}"))
                db.commit()
            client = app.test_client()
            self.assertEqual(client.get("/schedule").status_code, 200)
            with app.app_context():
                original_deadline = get_db().execute(
                    "SELECT deadline FROM lectures WHERE id=2").fetchone()[0]
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            earlier = (date.today() - timedelta(days=2)).isoformat()
            response = client.post("/tasks/1/complete", data={
                "completed_at": yesterday, "return_to_course": "1",
            })
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.location.endswith("/courses/1"))
            with app.app_context():
                db = get_db()
                task = db.execute("SELECT completed_at, remaining_hours FROM lecture_tasks WHERE id=1").fetchone()
                self.assertEqual((task["completed_at"], task["remaining_hours"]), (yesterday, 0))
                self.assertLess(db.execute("SELECT deadline FROM lectures WHERE id=2").fetchone()[0],
                                original_deadline)
                self.assertEqual(db.execute("SELECT remaining_hours FROM lectures WHERE id=1").fetchone()[0], 4)
            page = client.get("/courses/1").get_data(as_text=True)
            self.assertIn(f'value="{yesterday}"', page)
            self.assertIn("Cập nhật ngày hoàn thành", page)
            client.post("/tasks/1/complete", data={
                "completed_at": earlier, "return_to_course": "1",
            })
            with app.app_context():
                db = get_db()
                self.assertEqual(db.execute(
                    "SELECT completed_at FROM lecture_tasks WHERE id=1").fetchone()[0], earlier)
                self.assertTrue(db.execute("""SELECT 1 FROM schedule_events
                    WHERE task_id=1 AND reason='Chỉnh ngày hoàn thành phần việc'""").fetchone())
                correction = db.execute("""SELECT old_date, new_date FROM completion_events
                    WHERE task_id=1 ORDER BY id DESC LIMIT 1""").fetchone()
                self.assertEqual(tuple(correction), (yesterday, earlier))
                self.assertEqual(len(report_data()["completion_events"]), 2)
            client.post("/tasks/2/complete", data={
                "completed_at": yesterday, "return_to_course": "1",
            })
            with app.app_context():
                db = get_db()
                self.assertEqual(db.execute(
                    "SELECT completed_at FROM lectures WHERE id=1").fetchone()[0], yesterday)
            client.post("/lectures/1/complete", data={
                "completed_at": date.today().isoformat(), "return_to_course": "1",
            })
            with app.app_context():
                self.assertEqual(get_db().execute(
                    "SELECT completed_at FROM lectures WHERE id=1").fetchone()[0],
                                 date.today().isoformat())
            future = (date.today() + timedelta(days=1)).isoformat()
            client.post("/tasks/1/complete", data={"completed_at": future})
            with app.app_context():
                self.assertEqual(get_db().execute(
                    "SELECT completed_at FROM lecture_tasks WHERE id=1").fetchone()[0], earlier)

    def test_lecture_form_can_be_saved_and_new_lecture_added(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True,
                              "DATABASE": str(Path(directory) / "journal.sqlite3")})
            with app.app_context():
                db = get_db()
                db.execute("INSERT INTO courses (name) VALUES ('Khóa thử')")
                db.commit()
            client = app.test_client()
            self.assertEqual(client.post("/courses/1/lectures", data={
                "lecture_number": "1", "title": "Bài đầu", "remaining_hours": "3",
            }).status_code, 302)
            self.assertEqual(client.post("/lectures/1", data={
                "title": "Bài đã sửa", "status": "Đang học", "remaining_hours": "4",
                "change_reason": "Cần thêm bài tập",
            }).status_code, 302)
            with app.app_context():
                lecture = get_db().execute(
                    "SELECT title, remaining_hours FROM lectures WHERE id=1").fetchone()
                self.assertEqual(tuple(lecture), ("Bài đã sửa", 0))
            page = client.get("/courses/1").get_data(as_text=True)
            self.assertIn("Bài này đang có 0 giờ", page)
            self.assertNotIn('name="remaining_hours"', page)

    def test_insert_review_lesson_shifts_later_lessons_without_losing_their_data(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True,
                              "DATABASE": str(Path(directory) / "journal.sqlite3")})
            with app.app_context():
                db = get_db()
                db.execute("INSERT INTO courses (name) VALUES ('Khóa cần ôn tập')")
                db.execute("INSERT INTO plan_courses (plan_id, course_id, position) VALUES (1, 1, 1)")
                db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, title, status, estimated_hours,
                     remaining_hours, completed_at)
                    VALUES (1, 11, 'Bài 11', 'Hoàn thành', 1, 0, ?)""",
                    ((date.today() - timedelta(days=30)).isoformat(),))
                old_lesson_id = db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, title, estimated_hours, remaining_hours)
                    VALUES (1, 12, 'Bài 12', 1, 1)""").lastrowid
                task_id = db.execute("""INSERT INTO lecture_tasks
                    (lecture_id, position, kind, title, estimated_hours, remaining_hours)
                    VALUES (?, 1, 'video', 'Video của bài cũ', 1, 1)""",
                    (old_lesson_id,)).lastrowid
                db.commit()
            client = app.test_client()
            client.get("/schedule")
            with app.app_context():
                inherited_deadline = get_db().execute(
                    "SELECT deadline FROM lectures WHERE id=?", (old_lesson_id,)).fetchone()[0]
            response = client.post("/courses/1/lectures", data={
                "insert_before": "12",
                "lecture_number": "99",
                "title": "Ôn tập Bài 1–11",
                "content": "Làm lại bài sai và tổng hợp công thức",
                "estimated_hours": "3",
            }, follow_redirects=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn("Đã chèn bài mới ở vị trí 12", response.get_data(as_text=True))
            self.assertIn("Chèn trước Bài 12", response.get_data(as_text=True))
            with app.app_context():
                db = get_db()
                rows = db.execute("""SELECT id, lecture_number, title, status, completed_at
                    FROM lectures ORDER BY lecture_number""").fetchall()
                self.assertEqual([(row["lecture_number"], row["title"]) for row in rows],
                                 [(11, "Bài 11"), (12, "Ôn tập Bài 1–11"), (13, "Bài 13")])
                shifted = db.execute("SELECT * FROM lectures WHERE id=?", (old_lesson_id,)).fetchone()
                self.assertEqual((shifted["lecture_number"], shifted["title"]), (13, "Bài 13"))
                self.assertGreaterEqual(shifted["deadline"], inherited_deadline)
                self.assertGreaterEqual(db.execute("""SELECT MIN(study_date)
                    FROM schedule_allocations WHERE lecture_id=?""",
                    (old_lesson_id,)).fetchone()[0], inherited_deadline)
                inserted = db.execute("SELECT * FROM lectures WHERE lecture_number=12").fetchone()
                self.assertEqual((inserted["deadline"], inserted["manual_deadline"]),
                                 (inherited_deadline, inherited_deadline))
                inherited_event = db.execute("""SELECT old_deadline, new_deadline, kind
                    FROM schedule_events WHERE lecture_id=? AND kind='kế thừa hạn'""",
                    (inserted["id"],)).fetchone()
                self.assertEqual(tuple(inherited_event),
                                 (None, inherited_deadline, "kế thừa hạn"))
                self.assertEqual(db.execute("SELECT lecture_id FROM lecture_tasks WHERE id=?",
                                            (task_id,)).fetchone()[0], old_lesson_id)
                completed = rows[0]
                self.assertEqual(completed["status"], "Hoàn thành")
                self.assertIsNotNone(completed["completed_at"])
                self.assertEqual(db.execute("SELECT SUM(hours) FROM schedule_allocations").fetchone()[0],
                                 1)

    def test_inserting_lesson_never_reschedules_open_work_into_past_course_days(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True,
                              "DATABASE": str(Path(directory) / "journal.sqlite3")})
            today = date.today()
            yesterday = (today - timedelta(days=1)).isoformat()
            with app.app_context():
                db = get_db()
                db.execute("""INSERT INTO courses (name, start_date, status)
                    VALUES ('Khóa có lịch quá khứ', ?, 'Đang học')""",
                           ((today - timedelta(days=30)).isoformat(),))
                db.execute("INSERT INTO plan_courses (plan_id, course_id, position) VALUES (1, 1, 1)")
                first = db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, title, estimated_hours, remaining_hours, deadline)
                    VALUES (1, 1, 'Việc đang học', 1.5, 1.5, ?)""",
                                   (today.isoformat(),)).lastrowid
                task = db.execute("""INSERT INTO lecture_tasks
                    (lecture_id, position, kind, title, estimated_hours, remaining_hours, deadline)
                    VALUES (?, 1, 'video', 'Phần việc 1,5 giờ', 1.5, 1.5, ?)""",
                                  (first, today.isoformat())).lastrowid
                db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, title, estimated_hours, remaining_hours, deadline)
                    VALUES (1, 2, 'Bài phía sau', 1, 1, ?)""",
                           ((today + timedelta(days=1)).isoformat(),))
                # This is the split the user saw before inserting another lesson.
                db.execute("""INSERT INTO schedule_allocations
                    (course_id, lecture_id, task_id, study_date, hours)
                    VALUES (1, ?, ?, ?, .5)""", (first, task, yesterday))
                db.execute("""INSERT INTO schedule_allocations
                    (course_id, lecture_id, task_id, study_date, hours)
                    VALUES (1, ?, ?, ?, 1)""", (first, task, today.isoformat()))
                db.commit()

            response = app.test_client().post("/courses/1/lectures", data={
                "insert_before": "2", "lecture_number": "99", "title": "Bài ôn tập chèn thêm",
            })
            self.assertEqual(response.status_code, 302)
            with app.app_context():
                db = get_db()
                updated = db.execute(
                    "SELECT deadline FROM lecture_tasks WHERE id=?", (task,)).fetchone()
                self.assertGreaterEqual(updated["deadline"], today.isoformat())
                self.assertIsNone(db.execute("""SELECT MIN(study_date)
                    FROM schedule_allocations WHERE task_id=? AND study_date<?""",
                                             (task, today.isoformat())).fetchone()[0])
                self.assertAlmostEqual(db.execute("""SELECT SUM(hours)
                    FROM schedule_allocations WHERE task_id=?""", (task,)).fetchone()[0], 1.5)

    def test_tasks_can_be_added_manually_inside_each_lesson(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True,
                              "DATABASE": str(Path(directory) / "journal.sqlite3")})
            with app.app_context():
                db = get_db()
                db.execute("INSERT INTO courses (name) VALUES ('Khóa thêm phần việc')")
                db.execute("INSERT INTO plan_courses (plan_id, course_id, position) VALUES (1, 1, 1)")
                db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, title, estimated_hours, remaining_hours)
                    VALUES (1, 1, 'Bài thử nghiệm', 3, 3)""")
                db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, title, status, estimated_hours,
                     remaining_hours, completed_at)
                    VALUES (1, 2, 'Bài đã xong', 'Hoàn thành', 1, 0, ?)""",
                    (date.today().isoformat(),))
                db.commit()
            client = app.test_client()
            client.get("/schedule")
            page = client.get("/courses/1").get_data(as_text=True)
            self.assertIn("Thêm phần việc", page)
            self.assertIn('/lectures/1/tasks', page)

            first = client.post("/lectures/1/tasks", data={
                "kind": "video",
                "title": "Xem video bài giảng",
                "content": "Ghi chú các định nghĩa quan trọng",
                "estimated_hours": "1.5",
            }, follow_redirects=True)
            self.assertIn("Đã thêm phần việc", first.get_data(as_text=True))
            second = client.post("/lectures/1/tasks", data={
                "kind": "exercises",
                "title": "Làm bài tập",
                "content": "Hoàn thành bộ bài tập",
                "estimated_hours": "2",
            }, follow_redirects=True)
            self.assertEqual(second.status_code, 200)

            with app.app_context():
                db = get_db()
                tasks = db.execute("""SELECT position, kind, title, estimated_hours,
                    remaining_hours, deadline FROM lecture_tasks
                    WHERE lecture_id=1 ORDER BY position""").fetchall()
                self.assertEqual([(row["position"], row["kind"], row["title"])
                                  for row in tasks],
                                 [(1, "video", "Xem video bài giảng"),
                                  (2, "exercises", "Làm bài tập")])
                self.assertEqual([(row["estimated_hours"], row["remaining_hours"])
                                  for row in tasks], [(1.5, 1.5), (2, 2)])
                self.assertTrue(all(row["deadline"] for row in tasks))
                lecture = db.execute("""SELECT estimated_hours, remaining_hours
                    FROM lectures WHERE id=1""").fetchone()
                self.assertEqual(tuple(lecture), (3.5, 3.5))
                self.assertEqual(db.execute("""SELECT SUM(hours)
                    FROM schedule_allocations WHERE lecture_id=1""").fetchone()[0], 3.5)
                self.assertEqual(db.execute("""SELECT COUNT(*) FROM schedule_events
                    WHERE kind='thêm phần việc' AND lecture_id=1""").fetchone()[0], 2)

            rejected = client.post("/lectures/2/tasks", data={
                "kind": "review", "title": "Không được thêm", "estimated_hours": "1",
            }, follow_redirects=True)
            self.assertIn("Không thể thêm phần việc vào bài học đã hoàn thành",
                          rejected.get_data(as_text=True))
            with app.app_context():
                self.assertEqual(get_db().execute(
                    "SELECT COUNT(*) FROM lecture_tasks WHERE lecture_id=2").fetchone()[0], 0)

    def test_hour_totals_roll_up_and_drive_schedule(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True,
                              "DATABASE": str(Path(directory) / "journal.sqlite3")})
            with app.app_context():
                db = get_db()
                db.execute("INSERT INTO courses (name, estimated_hours) VALUES ('Khóa giờ học', 99)")
                db.execute("INSERT INTO plan_courses (plan_id, course_id, position) VALUES (1, 1, 1)")
                db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, estimated_hours, remaining_hours)
                    VALUES (1, 1, 6, 6)""")
                db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, estimated_hours, remaining_hours)
                    VALUES (1, 2, 2, 2)""")
                db.execute("""INSERT INTO lecture_tasks
                    (lecture_id, position, kind, title, estimated_hours, remaining_hours)
                    VALUES (1, 1, 'video', 'Xem video', 2, 2)""")
                db.execute("""INSERT INTO lecture_tasks
                    (lecture_id, position, kind, title, estimated_hours, remaining_hours)
                    VALUES (1, 2, 'exercises', 'Làm bài', 4, 4)""")
                db.commit()
            client = app.test_client()
            self.assertEqual(client.get("/schedule").status_code, 200)
            with app.app_context():
                course = courses_with_progress()[0]
                self.assertEqual((course["total_hours"], course["remaining_hours_total"]), (8, 8))
                scheduled = get_db().execute(
                    "SELECT SUM(hours) FROM schedule_allocations WHERE course_id=1").fetchone()[0]
                self.assertEqual(scheduled, 8)
            client.post("/tasks/1/complete", data={"completed_at": date.today().isoformat()})
            with app.app_context():
                course = courses_with_progress()[0]
                self.assertEqual((course["total_hours"], course["remaining_hours_total"]), (8, 6))
                self.assertEqual(course["display_progress"], 25)


if __name__ == "__main__":
    unittest.main()
