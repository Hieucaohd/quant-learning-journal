import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import create_app
from app.db import get_db
from app.scheduler import replan


class ScheduleTest(unittest.TestCase):
    def test_start_date_can_recalculate_same_date_in_past(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True,
                              "DATABASE": str(Path(directory) / "journal.sqlite3")})
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            with app.app_context():
                db = get_db()
                db.execute("INSERT INTO courses (name, start_date) VALUES ('Kiểm tra lịch', ?)",
                           (yesterday,))
                db.execute("INSERT INTO plan_courses (plan_id, course_id, position) VALUES (1, 1, 1)")
                db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, estimated_hours, remaining_hours, deadline)
                    VALUES (1, 1, 4, 4, '2099-01-01')""")
                db.commit()
            response = app.test_client().post("/courses/1/start-date",
                                              data={"start_date": yesterday},
                                              follow_redirects=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn("1 bài học", response.get_data(as_text=True))
            with app.app_context():
                self.assertEqual(get_db().execute(
                    "SELECT deadline FROM lectures WHERE id=1").fetchone()[0],
                    yesterday)
            response = app.test_client().post("/courses/1/start-date",
                                              data={"start_date": yesterday},
                                              follow_redirects=True)
            self.assertIn("hạn của các bài học chưa hoàn thành không đổi",
                          response.get_data(as_text=True))
            self.assertIn("Lưu ngày bắt đầu và tính lại lịch", response.get_data(as_text=True))

    def test_changing_past_start_moves_calendar_deadline_and_projection_together(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True,
                              "DATABASE": str(Path(directory) / "journal.sqlite3")})
            first = date.today() - timedelta(days=7)
            second = first + timedelta(days=1)
            with app.app_context():
                db = get_db()
                db.execute("INSERT INTO courses (name) VALUES ('Khóa kiểm tra lịch')")
                db.execute("INSERT INTO plan_courses (plan_id, course_id, position) VALUES (1, 1, 1)")
                db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, estimated_hours, remaining_hours,
                     status, completed_at) VALUES (1, 1, 1, 0, 'Hoàn thành', ?)""",
                    ((first - timedelta(days=1)).isoformat(),))
                db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, estimated_hours, remaining_hours)
                    VALUES (1, 2, 1, 1)""")
                db.execute("""INSERT INTO lecture_tasks
                    (lecture_id, position, kind, title, estimated_hours, remaining_hours)
                    VALUES (2, 1, 'video', 'Xem video kiểm tra', 1, 1)""")
                db.commit()
            client = app.test_client()
            client.post("/courses/1/start-date", data={"start_date": first.isoformat()})
            self.assertIn("Xem video kiểm tra", client.get(
                "/schedule", query_string={"date": first.isoformat()}).get_data(as_text=True))
            response = client.post("/courses/1/start-date",
                                   data={"start_date": second.isoformat()},
                                   follow_redirects=True)
            self.assertIn("1 bài học, 1 phần việc", response.get_data(as_text=True))
            with app.app_context():
                db = get_db()
                self.assertEqual(db.execute("SELECT target_date FROM courses WHERE id=1").fetchone()[0],
                                 second.isoformat())
                self.assertEqual(db.execute("SELECT deadline FROM lectures WHERE id=2").fetchone()[0],
                                 second.isoformat())
                self.assertEqual(db.execute("SELECT deadline FROM lecture_tasks WHERE id=1").fetchone()[0],
                                 second.isoformat())
                self.assertEqual(db.execute("SELECT completed_at FROM lectures WHERE id=1").fetchone()[0],
                                 (first - timedelta(days=1)).isoformat())
                self.assertEqual(db.execute("SELECT COUNT(*) FROM schedule_allocations WHERE study_date=?",
                                            (first.isoformat(),)).fetchone()[0], 0)
            old_day = client.get("/schedule", query_string={"date": first.isoformat()}).get_data(as_text=True)
            new_day = client.get("/schedule", query_string={"date": second.isoformat()}).get_data(as_text=True)
            self.assertIn("Chưa có việc học được lên lịch cho ngày này", old_day)
            self.assertIn("Xem video kiểm tra", new_day)

    def test_replan_does_not_duplicate_unfinished_hours_from_past_days(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True,
                              "DATABASE": str(Path(directory) / "journal.sqlite3")})
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            with app.app_context():
                db = get_db()
                db.execute("INSERT INTO courses (name) VALUES ('Khóa có việc tồn')")
                db.execute("INSERT INTO plan_courses (plan_id, course_id, position) VALUES (1, 1, 1)")
                db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, estimated_hours, remaining_hours)
                    VALUES (1, 1, 8, 8)""")
                db.execute("""INSERT INTO lecture_tasks
                    (lecture_id, position, kind, title, estimated_hours, remaining_hours)
                    VALUES (1, 1, 'video', 'Xem bài', 8, 8)""")
                db.commit()
            app.test_client().post("/courses/1/start-date", data={"start_date": yesterday})
            with app.app_context():
                db = get_db()
                self.assertGreater(db.execute("""SELECT COUNT(*) FROM schedule_allocations
                    WHERE task_id=1 AND study_date=?""", (yesterday,)).fetchone()[0], 0)
                replan(date.today(), "Tính lại sau khi đổi giờ học",
                       preserve_overdue=True, recalculate_overdue_course_id=1)
                db.commit()
                allocations = db.execute("""SELECT MIN(study_date), MAX(study_date), SUM(hours)
                    FROM schedule_allocations WHERE task_id=1""").fetchone()
                self.assertGreaterEqual(allocations[0], date.today().isoformat())
                self.assertEqual(allocations[2], 8)
                self.assertEqual(db.execute("SELECT deadline FROM lecture_tasks WHERE id=1").fetchone()[0],
                                 allocations[1])

    def test_changing_one_course_keeps_other_course_overdue_work_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True,
                              "DATABASE": str(Path(directory) / "journal.sqlite3")})
            old_day = (date.today() - timedelta(days=7)).isoformat()
            new_day = (date.today() - timedelta(days=1)).isoformat()
            with app.app_context():
                db = get_db()
                db.execute("INSERT INTO courses (name, start_date) VALUES ('Khóa quá hạn', ?)",
                           (old_day,))
                db.execute("INSERT INTO courses (name, start_date) VALUES ('Khóa đổi ngày', ?)",
                           (date.today().isoformat(),))
                db.execute("INSERT INTO plan_courses VALUES (1, 1, 1)")
                db.execute("INSERT INTO plan_courses VALUES (1, 2, 2)")
                db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, estimated_hours, remaining_hours)
                    VALUES (1, 1, 1, 1), (2, 1, 1, 1)""")
                replan(date.fromisoformat(old_day), "Lịch ban đầu")
                db.commit()
            app.test_client().post("/courses/2/start-date", data={"start_date": new_day})
            with app.app_context():
                db = get_db()
                self.assertEqual(db.execute("SELECT deadline FROM lectures WHERE id=1").fetchone()[0],
                                 old_day)
                self.assertEqual(db.execute("""SELECT study_date FROM schedule_allocations
                    WHERE lecture_id=1""").fetchone()[0], old_day)
                self.assertEqual(db.execute("SELECT target_date FROM courses WHERE id=1").fetchone()[0],
                                 old_day)
                self.assertEqual(db.execute("SELECT deadline FROM lectures WHERE id=2").fetchone()[0],
                                 new_day)

    def test_next_course_in_same_plan_waits_for_its_start(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True,
                              "DATABASE": str(Path(directory) / "journal.sqlite3")})
            future = (date.today() + timedelta(days=5)).isoformat()
            with app.app_context():
                db = get_db()
                db.execute("INSERT INTO courses (name, estimated_hours) VALUES ('Khóa trước', 1)")
                db.execute("""INSERT INTO courses (name, estimated_hours, start_date)
                    VALUES ('Khóa sau', 1, ?)""", (future,))
                db.execute("INSERT INTO plan_courses VALUES (1, 1, 1)")
                db.execute("INSERT INTO plan_courses VALUES (1, 2, 2)")
                db.commit()
            app.test_client().get("/schedule")
            with app.app_context():
                db = get_db()
                self.assertGreaterEqual(db.execute("""SELECT MIN(study_date)
                    FROM schedule_allocations WHERE course_id=2""").fetchone()[0], future)

    def test_editable_item_dates_and_historical_completion_before_start(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True,
                              "DATABASE": str(Path(directory) / "journal.sqlite3")})
            start_day = date.today() + timedelta(days=10)
            while start_day.weekday() != 0:
                start_day += timedelta(days=1)
            start = start_day.isoformat()
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            with app.app_context():
                db = get_db()
                db.execute("""INSERT INTO courses (name, start_date)
                    VALUES ('Khóa bắt đầu sau', ?)""", (start,))
                db.execute("INSERT INTO plan_courses (plan_id, course_id, position) VALUES (1, 1, 1)")
                for number in (1, 2):
                    db.execute("""INSERT INTO lectures
                        (course_id, lecture_number, estimated_hours, remaining_hours)
                        VALUES (1, ?, ?, ?)""", (number, 12 if number == 1 else 8,
                                                  12 if number == 1 else 8))
                for position, hours in ((1, 8), (2, 4)):
                    db.execute("""INSERT INTO lecture_tasks
                        (lecture_id, position, kind, title, estimated_hours, remaining_hours)
                        VALUES (1, ?, 'video', ?, ?, ?)""",
                               (position, f"Phần {position}", hours, hours))
                db.commit()
            client = app.test_client()
            client.get("/schedule")
            with app.app_context():
                db = get_db()
                original = db.execute("SELECT target_date FROM courses WHERE id=1").fetchone()[0]
                task_due = db.execute(
                    "SELECT deadline FROM lecture_tasks WHERE id=1").fetchone()[0]
            later = (date.fromisoformat(original) + timedelta(days=3)).isoformat()
            self.assertEqual(client.post("/tasks/1/deadline", data={
                "deadline": later, "reason": "Muốn thêm thời gian",
            }).status_code, 302)
            with app.app_context():
                db = get_db()
                self.assertEqual(db.execute(
                    "SELECT deadline FROM lecture_tasks WHERE id=1").fetchone()[0], later)
                self.assertGreaterEqual(db.execute(
                    "SELECT target_date FROM courses WHERE id=1").fetchone()[0], later)
                manual_events = db.execute("""SELECT old_deadline, new_deadline, reason, kind
                    FROM schedule_events WHERE task_id=1 AND old_deadline=? AND new_deadline=?""",
                    (task_due, later)).fetchall()
                self.assertEqual(len(manual_events), 1)
                self.assertEqual(tuple(manual_events[0]),
                                 (task_due, later, "Muốn thêm thời gian", "đặt hạn thủ công"))
            course_page = client.get("/courses/1").get_data(as_text=True)
            self.assertIn('<details class="deadline-editor">', course_page)
            self.assertIn("Hạn cũ, hạn mới, lý do và thời điểm lưu", course_page)
            impossible = start
            client.post("/tasks/1/deadline", data={
                "deadline": impossible, "reason": "Quá sớm",
            })
            with app.app_context():
                self.assertEqual(get_db().execute(
                    "SELECT manual_deadline FROM lecture_tasks WHERE id=1").fetchone()[0], later)
            client.post("/tasks/1/deadline", data={
                "deadline": "", "reason": "Quay về lịch tự động",
            })
            with app.app_context():
                db = get_db()
                task = db.execute("""SELECT manual_deadline, deadline
                    FROM lecture_tasks WHERE id=1""").fetchone()
                self.assertIsNone(task["manual_deadline"])
                self.assertEqual(task["deadline"], task_due)
            client.post("/tasks/1/deadline", data={
                "deadline": later, "reason": "Dời hạn lần nữa",
            })
            self.assertEqual(client.post("/tasks/1/deadline", data={
                "deadline": yesterday, "reason": "Đã học trước",
                "return_to_course": "1",
            }).status_code, 302)
            with app.app_context():
                db = get_db()
                task = db.execute("""SELECT status, completed_at, remaining_hours
                    FROM lecture_tasks WHERE id=1""").fetchone()
                self.assertEqual(tuple(task), ("Hoàn thành", yesterday, 0))
                self.assertLess(db.execute(
                    "SELECT target_date FROM courses WHERE id=1").fetchone()[0], later)
                self.assertEqual(db.execute("""SELECT COUNT(*) FROM schedule_allocations
                    WHERE task_id=1 AND study_date>=?""", (date.today().isoformat(),)).fetchone()[0], 0)
            self.assertEqual(client.post("/lectures/1/deadline", data={
                "deadline": yesterday, "reason": "Đã xong bài", "return_to_course": "1",
            }).status_code, 302)
            with app.app_context():
                db = get_db()
                self.assertEqual(db.execute(
                    "SELECT status FROM lectures WHERE id=1").fetchone()[0], "Hoàn thành")
                self.assertEqual(db.execute(
                    "SELECT status FROM lecture_tasks WHERE id=2").fetchone()[0], "Hoàn thành")
                self.assertTrue(db.execute(
                    "SELECT target_date FROM courses WHERE id=1").fetchone()[0])
            client.post("/lectures/2/deadline", data={
                "deadline": yesterday, "reason": "Đã học từ trước", "return_to_course": "1",
            })
            with app.app_context():
                db = get_db()
                course = db.execute(
                    "SELECT status, target_date FROM courses WHERE id=1").fetchone()
                self.assertEqual(tuple(course), ("Hoàn thành", yesterday))
                self.assertEqual(db.execute("""SELECT COUNT(*) FROM schedule_allocations
                    WHERE course_id=1 AND study_date>=?""",
                    (date.today().isoformat(),)).fetchone()[0], 0)

    def test_course_start_date_sets_first_allocation_and_calculates_finish(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True,
                              "DATABASE": str(Path(directory) / "journal.sqlite3")})
            with app.app_context():
                db = get_db()
                db.execute("INSERT INTO courses (name, status) VALUES ('Khóa có ngày bắt đầu', 'Đang học')")
                db.execute("INSERT INTO plan_courses (plan_id, course_id, position) VALUES (1, 1, 1)")
                overdue = (date.today() - timedelta(days=1)).isoformat()
                db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, estimated_hours, remaining_hours,
                     deadline, manual_deadline)
                    VALUES (1, 1, 12, 12, ?, '2099-12-31')""", (overdue,))
                db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, estimated_hours, remaining_hours, deadline)
                    VALUES (1, 2, 4, 4, ?)""", (overdue,))
                db.execute("""INSERT INTO lecture_tasks
                    (lecture_id, position, kind, title, estimated_hours, remaining_hours,
                     deadline, manual_deadline)
                    VALUES (2, 1, 'video', 'Xem bài', 4, 4, ?, '2099-12-31')""",
                    (overdue,))
                db.commit()
            client = app.test_client()
            future_start = (date.today() + timedelta(days=10)).isoformat()
            response = client.post("/courses/1/start-date", data={"start_date": future_start})
            self.assertEqual(response.status_code, 302)
            with app.app_context():
                db = get_db()
                bounds = db.execute("""SELECT MIN(study_date), MAX(study_date), SUM(hours)
                    FROM schedule_allocations WHERE course_id=1""").fetchone()
                course = db.execute(
                    "SELECT start_date, target_date FROM courses WHERE id=1").fetchone()
                self.assertGreaterEqual(bounds[0], future_start)
                self.assertEqual(bounds[1], course["target_date"])
                self.assertNotEqual(course["target_date"], "2099-12-31")
                self.assertEqual(bounds[2], 16)
                deadlines = [row[0] for row in db.execute(
                    "SELECT deadline FROM lectures ORDER BY id")]
                self.assertTrue(all(value >= future_start for value in deadlines))
                self.assertGreaterEqual(db.execute(
                    "SELECT deadline FROM lecture_tasks WHERE id=1").fetchone()[0], future_start)
                self.assertIsNone(db.execute(
                    "SELECT manual_deadline FROM lectures WHERE id=1").fetchone()[0])
                self.assertIsNone(db.execute(
                    "SELECT manual_deadline FROM lecture_tasks WHERE id=1").fetchone()[0])
                changes = db.execute("""SELECT old_deadline, new_deadline, reason
                    FROM schedule_events WHERE old_deadline=? AND kind='điều chỉnh'""",
                    (overdue,)).fetchall()
                self.assertEqual(len(changes), 3)
                self.assertTrue(all("Đổi ngày bắt đầu" in row["reason"] for row in changes))
                self.assertEqual(db.execute("""SELECT COUNT(*) FROM schedule_events
                    WHERE kind='bỏ hạn thủ công'""").fetchone()[0], 2)
                first_deadlines = deadlines
            later_start = (date.fromisoformat(future_start) + timedelta(days=7)).isoformat()
            client.post("/courses/1/start-date", data={"start_date": later_start})
            with app.app_context():
                db = get_db()
                second_deadlines = [row[0] for row in db.execute(
                    "SELECT deadline FROM lectures ORDER BY id")]
                self.assertTrue(all(new > old for old, new in zip(first_deadlines,
                                                                  second_deadlines)))
                self.assertGreaterEqual(db.execute(
                    "SELECT deadline FROM lecture_tasks WHERE id=1").fetchone()[0],
                    later_start)
            page = client.get("/courses/1").get_data(as_text=True)
            self.assertNotIn('name="target_date"', page)
            client.post("/courses/1", data={
                "name": "Khóa có ngày bắt đầu", "status": "Đang học", "progress": "0",
                "estimated_hours": "0", "target_date": "2099-12-31",
            })
            with app.app_context():
                self.assertNotEqual(get_db().execute(
                    "SELECT target_date FROM courses WHERE id=1").fetchone()[0], "2099-12-31")

    def make_app(self, directory, hours=3):
        app = create_app({"TESTING": True,
                          "DATABASE": str(Path(directory) / "journal.sqlite3"),
                          "EXPORT_DIR": str(Path(directory) / "exports")})
        with app.app_context():
            db = get_db()
            db.execute("INSERT INTO courses (name) VALUES ('Khóa thử')")
            db.execute("INSERT INTO plan_courses (plan_id, course_id, position) VALUES (1, 1, 1)")
            db.execute("""INSERT INTO lectures
                (course_id, lecture_number, title, content, remaining_hours)
                VALUES (1, 1, 'Bài thử', 'Giải bài tập', ?)""", (hours,))
            db.execute("UPDATE user_capacity_profiles SET hours_json='[1,1,1,1,1,1,1]' WHERE user_id=1")
            db.commit()
        return app

    def test_daily_allocation_and_capacity_replan_history(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory)
            client = app.test_client()
            self.assertEqual(client.get("/schedule").status_code, 200)
            with app.app_context():
                db = get_db()
                old_deadline = db.execute("SELECT deadline FROM lectures WHERE id=1").fetchone()[0]
                allocations = db.execute(
                    "SELECT study_date, hours FROM schedule_allocations ORDER BY study_date"
                ).fetchall()
                self.assertEqual(len(allocations), 3)
                self.assertTrue(all(row["hours"] == 1 for row in allocations))
            effective = (date.today() + timedelta(days=1)).isoformat()
            form = {"effective_from": effective, "reason": "Giảm thời gian học"}
            form.update({f"day_{day}": "0.25" for day in range(7)})
            self.assertEqual(client.post("/schedule/capacity", data=form).status_code, 302)
            with app.app_context():
                db = get_db()
                new_deadline = db.execute("SELECT deadline FROM lectures WHERE id=1").fetchone()[0]
                self.assertGreater(new_deadline, old_deadline)
                event = db.execute("""SELECT old_deadline, new_deadline, reason
                    FROM schedule_events WHERE old_deadline IS NOT NULL ORDER BY id DESC LIMIT 1""").fetchone()
                self.assertEqual((event["old_deadline"], event["new_deadline"]),
                                 (old_deadline, new_deadline))
                self.assertIn("Giảm thời gian học", event["reason"])
                self.assertEqual(db.execute(
                    "SELECT COUNT(*) FROM user_capacity_events WHERE user_id=1").fetchone()[0], 1)

    def test_missed_deadline_reason_and_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory, hours=1)
            client = app.test_client()
            self.assertEqual(client.get("/schedule").status_code, 200)
            today = date.today().isoformat()
            with app.app_context():
                self.assertEqual(get_db().execute(
                    "SELECT deadline FROM lectures WHERE id=1").fetchone()[0], today)
            self.assertEqual(client.post("/lectures/1/miss", data={
                "reason": "Cần ôn lại kiến thức nền", "return_date": today,
            }).status_code, 302)
            with app.app_context():
                db = get_db()
                moved = db.execute("SELECT deadline FROM lectures WHERE id=1").fetchone()[0]
                self.assertGreater(moved, today)
                missed = db.execute("SELECT * FROM missed_deadlines").fetchone()
                self.assertEqual(missed["old_deadline"], today)
                self.assertEqual(missed["new_deadline"], moved)
                self.assertIn("Cần ôn lại", missed["reason"])
            self.assertEqual(client.post("/lectures/1/complete", data={
                "completed_at": today, "return_date": today,
            }).status_code, 302)
            with app.app_context():
                db = get_db()
                lecture = db.execute("SELECT status, completed_at, completed_on_time FROM lectures").fetchone()
                self.assertEqual(tuple(lecture), ("Hoàn thành", today, 1))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM schedule_allocations WHERE study_date>?",
                                            (today,)).fetchone()[0], 0)
            response = client.post("/export")
            self.assertEqual(response.status_code, 200)
            exported = json.loads((Path(directory) / "exports" / "learning_data.json").read_text(
                encoding="utf-8"))
            self.assertEqual(len(exported["missed_deadlines"]), 1)
            self.assertGreater(len(exported["schedule_events"]), 0)

    def test_single_day_override_moves_work_and_keeps_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory, hours=1)
            client = app.test_client()
            self.assertEqual(client.get("/schedule").status_code, 200)
            today = date.today().isoformat()
            response = client.post("/schedule/override", data={
                "study_date": today, "hours": "0", "reason": "Có việc gia đình",
            })
            self.assertEqual(response.status_code, 302)
            with app.app_context():
                db = get_db()
                deadline = db.execute("SELECT deadline FROM lectures WHERE id=1").fetchone()[0]
                self.assertGreater(deadline, today)
                self.assertEqual(db.execute("""SELECT COUNT(*) FROM schedule_allocations
                    WHERE study_date=?""", (today,)).fetchone()[0], 0)
                event = db.execute("SELECT reason FROM schedule_events ORDER BY id DESC LIMIT 1").fetchone()
                self.assertIn("Có việc gia đình", event["reason"])

    def test_calendar_precedes_overdue_section_and_supports_inline_day_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.make_app(directory, hours=1)
            client = app.test_client()
            client.get("/schedule")
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            with app.app_context():
                db = get_db()
                db.execute("UPDATE lectures SET deadline=? WHERE id=1", (yesterday,))
                db.commit()
            page = client.get("/schedule").get_data(as_text=True)
            self.assertLess(page.index('class="calendar-grid"'),
                            page.index("Phần việc quá hạn cần xử lý"))
            self.assertIn('id="schedule-page"', page)
            self.assertIn('id="day-detail"', page)
            self.assertIn('/static/schedule.js', page)
            self.assertIn("Hôm nay mình cần làm gì?", page)
            self.assertIn("Mình đã bỏ lỡ gì?", page)
            self.assertIn("Có cần điều chỉnh gì không?", page)


if __name__ == "__main__":
    unittest.main()
