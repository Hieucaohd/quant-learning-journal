import gc
import json
import tempfile
import unittest
from datetime import date
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from flask import template_rendered

from app import create_app
from app.db import create_user, get_db, migrate_database


class HabitsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config = {"TESTING": True, "AUTH_DISABLED": False,
                       "DATABASE": str(root / "journal.sqlite3"),
                       "EXPORT_DIR": str(root / "exports"),
                       "APP_USERNAME": "admin", "APP_PASSWORD": "admin-password"}
        self.app = create_app(self.config)
        @self.app.before_request
        def optional_disable_cascades():
            if self.app.config.get("TEST_DISABLE_FKS"):
                get_db().execute("PRAGMA foreign_keys=OFF")
        self.client = self.app.test_client()
        self.client.post("/login", data={"username": "admin", "password": "admin-password"})
        self.today = date(2026, 11, 6)  # Friday
        for module in ("app.habits", "app.routes", "app.scheduler", "app.reports"):
            clock = patch(f"{module}.local_today", return_value=self.today)
            clock.start()
            self.addCleanup(clock.stop)
        response = self.client.post("/plans", data={"name": "Sức khỏe", "kind": "habit"})
        self.assertEqual(response.status_code, 302)
        self.plan_id = self.rows("SELECT id FROM study_plans WHERE kind='habit'")[0]["id"]
        self.plan_url = f"/plans/{self.plan_id}"

    def rows(self, sql, params=()):
        with self.app.app_context():
            return [dict(r) for r in get_db().execute(sql, params)]

    def add_habit(self, **overrides):
        data = {"title": "Tập thể dục", "weekday": ["0", "2", "4"],
                "start_date": "2026-11-02"}
        data.update(overrides)
        response = self.client.post(self.plan_url + "/habits", data=data, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('class="notice error"', response.get_data(as_text=True))
        return self.rows("SELECT id FROM habits ORDER BY id DESC")[0]["id"]

    def record(self, habit_id, day, status, **extra):
        return self.client.post(f"/habits/{habit_id}/entries",
                                data={"date": day, "status": status, **extra})

    def context(self, url):
        contexts = []
        def capture(sender, template, context, **kwargs):
            contexts.append(context)
        with template_rendered.connected_to(capture, self.app):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response, contexts[-1]

    def test_recurrence_statistics_and_streak_exclude_off_days_and_excused_rest(self):
        habit = self.add_habit()
        self.record(habit, "2026-11-02", "done")
        self.record(habit, "2026-11-04", "skipped", note="Nghỉ phục hồi")
        response, context = self.context(self.plan_url)
        self.assertEqual(context["week_stats"],
                         {"done": 1, "missed": 0, "pending": 1, "skipped": 1,
                          "total": 2, "percent": 50})
        self.assertEqual(context["habits"][0]["streak"], 1)
        self.assertEqual(len(context["day_habits"]), 1)
        _, off_day = self.context(self.plan_url + "?date=2026-11-03")
        self.assertEqual(off_day["day_habits"], [])
        self.record(habit, "2026-11-06", "done")
        _, context = self.context(self.plan_url)
        self.assertEqual(context["habits"][0]["streak"], 2)
        self.assertEqual(context["week_stats"]["percent"], 100)
        self.record(habit, "2026-11-04", "missed")
        _, context = self.context(self.plan_url)
        self.assertEqual(context["habits"][0]["streak"], 1)
        self.assertEqual(context["week_stats"]["total"], 3)
        self.assertEqual(context["week_stats"]["percent"], 67)

    def test_entry_updates_persist_and_reset_does_not_duplicate(self):
        habit = self.add_habit()
        for state in ("done", "missed", "skipped"):
            self.assertEqual(self.record(habit, "2026-11-02", state, note="Ghi chú").status_code, 302)
            self.assertEqual(len(self.rows("SELECT * FROM habit_entries")), 1)
            self.assertEqual(self.rows("SELECT status FROM habit_entries")[0]["status"], state)
        app = create_app(self.config)
        with app.app_context():
            self.assertEqual(get_db().execute("SELECT note FROM habit_entries").fetchone()[0], "Ghi chú")
        self.record(habit, "2026-11-02", "pending")
        self.assertEqual(self.rows("SELECT * FROM habit_entries"), [])

    def test_invalid_dates_states_and_recurrences_write_nothing(self):
        habit = self.add_habit()
        for day, state in (("2026-11-09", "done"), ("2026-11-03", "done"),
                           ("2026-10-30", "done"), ("bad", "done"), ("2026-11-02", "bogus")):
            self.record(habit, day, state)
        self.record(habit, "2026-11-02", "done", note="x" * 2001)
        self.assertEqual(self.rows("SELECT * FROM habit_entries"), [])
        for weekdays in ([], ["7"], ["x"]):
            self.client.post(self.plan_url + "/habits", data={"title": "Invalid",
                             "weekday": weekdays, "start_date": "2026-11-02"})
        self.assertEqual(len(self.rows("SELECT * FROM habits")), 1)
        response, context = self.context(self.plan_url + "?date=2026-11-09")
        self.assertNotIn(f'action="/habits/{habit}/entries"', response.get_data(as_text=True))
        self.assertEqual(context["week_stats"]["total"], 0)

    def test_calendar_filter_dashboard_and_no_effect_on_hourly_allocations(self):
        self.client.post("/courses", data={"name": "Toán", "estimated_hours": "4"})
        course = self.rows("SELECT id FROM courses")[0]["id"]
        self.client.post("/plans/1/courses", data={"course_id": course})
        before = self.rows("SELECT * FROM schedule_allocations")
        habit = self.add_habit()
        response = self.record(habit, "2026-11-06", "done", return_to="schedule",
                               filter="1", plan_id=str(self.plan_id))
        self.assertIn(f"plan_id={self.plan_id}", response.location)
        self.assertEqual(self.rows("SELECT * FROM schedule_allocations"), before)
        response, context = self.context(f"/schedule?filter=1&plan_id={self.plan_id}&date=2026-11-06")
        self.assertEqual(context["day_tasks"], [])
        self.assertEqual(context["day_habits"][0]["status"], "done")
        _, empty = self.context("/schedule?filter=1")
        self.assertEqual(empty["day_habits"], [])
        _, dashboard = self.context("/")
        self.assertEqual(dashboard["today_habits"][0]["id"], habit)
        self.assertEqual(dashboard["planned_hours"], 4)
        self.assertEqual(self.client.post(self.plan_url + "/courses", data={"course_id": course}).status_code, 400)
        self.assertEqual(self.client.post("/plans/1/habits", data={}).status_code, 404)

    def test_shared_habits_are_read_only_and_other_users_are_isolated(self):
        habit = self.add_habit()
        self.record(habit, "2026-11-06", "done", note="Riêng tư")
        with self.app.app_context():
            create_user("alice", "alice-password")
            create_user("bob", "bob-password")
            get_db().commit()
        self.client.post(self.plan_url + "/share", data={"username": "alice"})
        for user in ("alice", "bob"):
            self.client.post("/logout")
            self.client.post("/login", data={"username": user, "password": user + "-password"})
            self.assertEqual(self.client.get(self.plan_url).status_code, 404)
            shared = self.client.get(f"/shared/plans/{self.plan_id}")
            self.assertEqual(shared.status_code, 200 if user == "alice" else 403)
            self.assertNotIn(f'action="/habits/{habit}/entries"', shared.get_data(as_text=True))
            self.assertEqual(self.record(habit, "2026-11-06", "missed").status_code, 404)
            self.assertEqual(self.client.post(f"/habits/{habit}/end", data={"end_date":"2026-11-06"}).status_code, 404)
            schedule = self.client.get(f"/schedule?filter=1&plan_id={self.plan_id}")
            self.assertEqual(schedule.status_code, 200 if user == "alice" else 400)
            self.assertNotIn(f'action="/habits/{habit}/entries"', schedule.get_data(as_text=True))
            with ZipFile(BytesIO(self.client.post("/export").data)) as archive:
                data = json.loads(archive.read("learning_data.json"))
                self.assertEqual(data["habits"], [])
                self.assertEqual(data["habit_entries"], [])

    def test_end_and_pause_preserve_recorded_history_and_export(self):
        habit = self.add_habit()
        self.record(habit, "2026-11-04", "done", note="Đi bộ")
        self.client.post(f"/habits/{habit}/end", data={"end_date": "2026-11-02"})
        self.assertIsNone(self.rows("SELECT end_date FROM habits")[0]["end_date"])
        self.client.post(f"/habits/{habit}/end", data={"end_date": "2026-11-04"})
        _, context = self.context(self.plan_url)
        self.assertEqual(context["day_habits"], [])
        self.client.post(self.plan_url, data={"name": "Sức khỏe", "status": "Tạm dừng", "priority":"100"})
        _, history = self.context(f"/schedule?filter=1&plan_id={self.plan_id}&date=2026-11-04")
        self.assertEqual(history["day_habits"][0]["status"], "done")
        with ZipFile(BytesIO(self.client.post("/export").data)) as archive:
            data = json.loads(archive.read("learning_data.json"))
            self.assertEqual(data["habit_entries"][0]["note"], "Đi bộ")
            self.assertIn("Tập thể dục", archive.read("progress_report.md").decode())

    def test_plan_deletion_cleans_entries_even_without_foreign_key_cascades(self):
        habit = self.add_habit()
        self.record(habit, "2026-11-06", "done")
        self.app.config["TEST_DISABLE_FKS"] = True
        self.client.post(self.plan_url + "/delete", data={"name":"wrong"})
        self.assertEqual(len(self.rows("SELECT * FROM habits")), 1)
        self.client.post(self.plan_url + "/delete", data={"name":"Sức khỏe"})
        self.assertEqual(self.rows("SELECT * FROM habits"), [])
        self.assertEqual(self.rows("SELECT * FROM habit_entries"), [])

    def test_v3_upgrade_preserves_study_rows_and_is_repeatable(self):
        with self.app.app_context():
            db = get_db()
            before = [dict(r) for r in db.execute("SELECT id,name,status,owner_user_id FROM study_plans")]
            db.execute("DROP TABLE habit_entries")
            db.execute("DROP TABLE habits")
            db.execute("ALTER TABLE study_plans DROP COLUMN kind")
            db.execute("UPDATE app_meta SET value='3' WHERE key='schema_version'")
            db.commit()
            migrate_database()
            self.assertEqual([dict(r) for r in db.execute("SELECT id,name,status,owner_user_id FROM study_plans")], before)
            self.assertTrue(all(r[0] == "study" for r in db.execute("SELECT kind FROM study_plans")))
            db.execute("UPDATE app_meta SET value='3' WHERE key='schema_version'")
            db.commit()
            migrate_database()
            self.assertEqual(db.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()[0], "5")

    def test_v4_upgrade_adds_empty_exclusions_without_changing_existing_data(self):
        habit = self.add_habit()
        self.record(habit, "2026-11-06", "done", note="Giữ nguyên")
        with self.app.app_context():
            db = get_db()
            db.execute("ALTER TABLE habits DROP COLUMN excluded_dates_json")
            db.execute("UPDATE app_meta SET value='4' WHERE key='schema_version'")
            db.commit()
            before = {table: [dict(r) for r in db.execute(f"SELECT * FROM {table}")]
                      for table in ("habits", "habit_entries", "study_plans", "courses", "schedule_allocations")}
            with patch("app.db.migrate_multi_user", side_effect=AssertionError("Legacy migration must not run")):
                migrate_database()
            self.assertEqual(db.execute("SELECT excluded_dates_json FROM habits").fetchone()[0], "[]")
            for table, rows in before.items():
                after = [dict(r) for r in db.execute(f"SELECT * FROM {table}")]
                if table == "habits":
                    for row in after:
                        row.pop("excluded_dates_json")
                self.assertEqual(rows, after)
            db.execute("UPDATE app_meta SET value='4' WHERE key='schema_version'")
            db.commit()
            migrate_database()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM habits").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()[0], "5")

    def test_account_deletion_backs_up_and_removes_habit_results(self):
        with self.app.app_context():
            bob = create_user("bob", "bob-password")
            db = get_db()
            plan = db.execute("INSERT INTO study_plans(name,kind,owner_user_id) VALUES('Bob habit','habit',?)",
                              (bob,)).lastrowid
            habit = db.execute("""INSERT INTO habits(plan_id,title,weekdays_json,start_date)
                VALUES(?,'Walk','[4]','2026-11-06')""", (plan,)).lastrowid
            db.execute("INSERT INTO habit_entries(habit_id,entry_date,status) VALUES(?,'2026-11-06','done')",
                       (habit,))
            db.commit()
        self.app.config["TEST_DISABLE_FKS"] = True
        self.client.post(f"/admin/users/{bob}/delete", data={"confirm_username":"bob"})
        self.assertEqual(self.rows("SELECT * FROM habit_entries"), [])
        self.assertEqual(self.rows("SELECT * FROM habits"), [])
        snapshot = json.loads(self.rows("SELECT payload_json FROM deletion_backups WHERE label='user-bob'")[0]["payload_json"])
        self.assertEqual(snapshot["habits"][0]["title"], "Walk")
        self.assertEqual(snapshot["habit_entries"][0]["status"], "done")

    def test_libsql_backend_can_import_record_and_update_habits(self):
        with tempfile.TemporaryDirectory() as directory:
            config = dict(self.config, TURSO_DATABASE_URL=str(Path(directory)/"remote.db"),
                          TURSO_AUTH_TOKEN="test", AUTH_DISABLED=True)
            app = create_app(config)
            client = app.test_client()
            client.post("/plans", data={"name":"Remote habits", "kind":"habit"})
            client.post("/plans/2/habits", data={"title":"Exercise", "weekday":["4"], "start_date":"2026-11-06"})
            payload = {"schema_version": 1, "plan_type": "habit", "name": "Remote habits", "tasks": [
                {"title": "Exercise", "weekdays": [4], "start_date": "2026-11-06"},
                {"title": "Weekly review", "weekdays": [4], "start_date": "2026-11-06",
                 "excluded_dates": ["2026-11-13"]}]}
            self.assertEqual(client.post("/import-plan", data={"import_type": "habit", "mode": "confirm",
                             "payload": json.dumps(payload)}).status_code, 302)
            for state in ("done", "skipped"):
                self.assertEqual(client.post("/habits/1/entries", data={"date":"2026-11-06", "status":state}).status_code, 302)
            with app.app_context():
                self.assertEqual(get_db().execute("SELECT status FROM habit_entries").fetchone()[0], "skipped")
                self.assertEqual(get_db().execute("SELECT COUNT(*) FROM habits").fetchone()[0], 2)
                self.assertEqual(json.loads(get_db().execute(
                    "SELECT excluded_dates_json FROM habits WHERE title='Weekly review'").fetchone()[0]), ["2026-11-13"])
            del client, app
            gc.collect()


if __name__ == "__main__":
    unittest.main()
