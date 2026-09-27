import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import create_app
from app.db import get_db


class ProgressNumbersTest(unittest.TestCase):
    """Dashboard and review figures come from completed work, not only the journal."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True,
                               "DATABASE": str(Path(self.directory.name) / "journal.sqlite3")})
        self.client = self.app.test_client()
        self.today = date.today()
        self.yesterday = self.today - timedelta(days=1)
        self.long_ago = self.today - timedelta(days=40)
        with self.app.app_context():
            db = get_db()
            active = db.execute("""INSERT INTO courses (name, display_name, owner_user_id, status)
                VALUES ('Đại số', 'Đại số', 1, 'Chưa bắt đầu')""").lastrowid
            finished = db.execute("""INSERT INTO courses
                (name, display_name, owner_user_id, status, target_date)
                VALUES ('Khóa cũ', 'Khóa cũ', 1, 'Hoàn thành', ?)""",
                (self.long_ago.isoformat(),)).lastrowid
            # Lecture 1: one part done today (2h), one part still open (4h).
            lecture = db.execute("""INSERT INTO lectures (course_id, lecture_number, title,
                status, estimated_hours, remaining_hours) VALUES (?, 1, 'Ma trận', 'Đang học', 6, 4)""",
                (active,)).lastrowid
            db.execute("""INSERT INTO lecture_tasks (lecture_id, position, kind, title,
                estimated_hours, remaining_hours, status, completed_at)
                VALUES (?, 1, 'video', 'Xem bài giảng', 2, 0, 'Hoàn thành', ?),
                       (?, 2, 'exercises', 'Làm bài tập', 4, 4, 'Chưa bắt đầu', NULL)""",
                (lecture, self.today.isoformat(), lecture))
            # Lecture 2 has no parts and was finished yesterday (3h).
            db.execute("""INSERT INTO lectures (course_id, lecture_number, title, status,
                estimated_hours, remaining_hours, completed_at)
                VALUES (?, 2, 'Định thức', 'Hoàn thành', 3, 0, ?)""",
                (active, self.yesterday.isoformat()))
            # The finished course's only lecture was done long ago (1h).
            db.execute("""INSERT INTO lectures (course_id, lecture_number, title, status,
                estimated_hours, remaining_hours, completed_at)
                VALUES (?, 1, 'Ôn tập', 'Hoàn thành', 1, 0, ?)""",
                (finished, self.long_ago.isoformat()))
            db.execute("""INSERT INTO user_capacity_overrides (user_id, study_date, hours, reason)
                VALUES (1, ?, 0, 'Nghỉ')""", (self.today.isoformat(),))
            db.commit()

    def tearDown(self):
        self.directory.cleanup()

    def this_week(self, day):
        start = self.today - timedelta(days=self.today.weekday())
        return start <= day <= start + timedelta(days=6)

    def test_dashboard_counts_completed_work_and_real_capacity(self):
        page = self.client.get("/").get_data(as_text=True)
        # 10 planned hours (6 + 3 + 1); only the open 4h part remains.
        self.assertIn("6 <small>/ 10 giờ</small>", page)
        self.assertIn("60% khối lượng", page)
        self.assertIn("2 <small>ngày</small>", page)  # today and yesterday
        week_hours = 2 + (3 if self.this_week(self.yesterday) else 0)
        self.assertIn(f"{week_hours:g} <small>giờ</small>", page)
        default = [4, 4, 4, 4, 4, 8, 8]
        capacity = sum(default) - default[self.today.weekday()]  # today overridden to 0
        self.assertIn(f"quỹ thời gian tuần này {capacity:g} giờ", page)
        self.assertNotIn("700 giờ", page)
        self.assertNotIn("36 giờ mỗi tuần", page)

    def test_course_with_finished_work_is_shown_in_progress(self):
        page = self.client.get("/courses").get_data(as_text=True)
        self.assertIn("Đang học", page)
        self.assertNotIn("Chưa bắt đầu · 2 bài học", page)

    def test_reviews_count_completed_work_in_the_period(self):
        week = self.client.get(f"/reviews?period=week&date={self.today}").get_data(as_text=True)
        week_hours = 2 + (3 if self.this_week(self.yesterday) else 0)
        self.assertIn(f"{week_hours:g} <small>giờ</small>", week)
        self.assertIn("Xem bài giảng", week)
        self.assertNotIn("Ôn tập", week)

        old_month = self.client.get(
            f"/reviews?period=month&date={self.long_ago}").get_data(as_text=True)
        self.assertIn("Khóa học hoàn thành trong tháng: Khóa cũ", old_month)
        this_month = self.client.get(
            f"/reviews?period=month&date={self.today}").get_data(as_text=True)
        self.assertIn("Khóa học hoàn thành trong tháng: Chưa có", this_month)


if __name__ == "__main__":
    unittest.main()
