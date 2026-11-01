import copy
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
from app.db import create_user, get_db
from app.habit_import import parse_habit_plan


class HabitImportTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.app = create_app({"TESTING": True, "AUTH_DISABLED": False,
                               "DATABASE": str(Path(temp.name) / "journal.db"),
                               "APP_USERNAME": "admin", "APP_PASSWORD": "admin-password"})
        self.client = self.app.test_client()
        self.client.post("/login", data={"username": "admin", "password": "admin-password"})
        for module in ("habit_import", "habits", "routes", "reports", "scheduler"):
            clock = patch(f"app.{module}.local_today", return_value=date(2026, 11, 6))
            clock.start()
            self.addCleanup(clock.stop)
        self.payload = {"schema_version": 1, "plan_type": "habit", "name": "Sức khỏe",
                        "description": "Theo dõi hằng tuần", "tasks": [
                            {"title": "Tập thể dục", "description": "Vận động nhẹ", "weekdays": [4, 0, 2],
                             "start_date": "2026-11-02", "end_date": None},
                            {"title": "Tổng kết tuần", "weekdays": [4], "start_date": "2026-11-06",
                             "end_date": "2026-11-06"}]}

    def rows(self, sql, params=()):
        with self.app.app_context():
            return [dict(r) for r in get_db().execute(sql, params)]

    def submit(self, mode="confirm", payload=None, **extra):
        return self.client.post("/import-plan", data={"mode": mode, "import_type": "habit",
                                "payload": json.dumps(payload or self.payload, ensure_ascii=False), **extra})

    def context(self, url):
        contexts = []
        def capture(sender, template, context, **kwargs):
            contexts.append(context)
        with template_rendered.connected_to(capture, self.app):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True), contexts[-1]

    def test_preview_is_read_only_confirm_creates_plan_and_recurring_calendar_tasks(self):
        before = self.rows("SELECT * FROM study_plans")
        preview = self.submit(mode="preview").get_data(as_text=True)
        self.assertIn("Thêm 2 việc", preview)
        self.assertNotIn("giờ ước tính", preview)
        self.assertEqual(self.rows("SELECT * FROM study_plans"), before)
        self.assertEqual(self.rows("SELECT * FROM habits"), [])
        response = self.submit()
        self.assertEqual(response.status_code, 302)
        plan = self.rows("SELECT * FROM study_plans WHERE kind='habit'")[0]
        self.assertTrue(response.location.endswith(f"/plans/{plan['id']}"))
        self.assertEqual(self.rows("SELECT * FROM schedule_allocations"), [])
        page, context = self.context(f"/schedule?filter=1&plan_id={plan['id']}&date=2026-11-06")
        self.assertEqual(len(context["today_habits"]), 2)
        self.assertEqual(context["day_tasks"], [])
        self.assertIn("<strong>2 việc</strong>", page)
        self.assertIn("Mở phần việc trong kế hoạch", page)
        _, next_day = self.context(f"/schedule?filter=1&plan_id={plan['id']}&date=2026-11-07")
        self.assertEqual(next_day["day_habits"], [])
        # Today's count is independent of the month being viewed.
        _, next_month = self.context(f"/schedule?filter=1&plan_id={plan['id']}&date=2026-12-01")
        self.assertEqual(len(next_month["today_habits"]), 2)
        _, empty = self.context("/schedule?filter=1")
        self.assertEqual(empty["today_habits"], [])
        dashboard, _ = self.context("/")
        section = dashboard.split("Hôm nay cần làm gì?", 1)[1]
        self.assertIn("Tập thể dục", section)
        self.assertNotIn("Hôm nay chưa có việc", section)

    def test_reimport_and_export_round_trip_preserve_existing_tasks_and_results(self):
        self.submit()
        habit = self.rows("SELECT * FROM habits ORDER BY id")[0]
        self.client.post(f"/habits/{habit['id']}/entries", data={"date": "2026-11-06", "status": "done", "note": "Đã tập"})
        before = self.rows("SELECT * FROM habit_entries")
        _, dashboard = self.context("/")
        self.assertEqual(dashboard["streak"], 1)
        with ZipFile(BytesIO(self.client.post("/export").data)) as archive:
            self.assertIn("Số ngày liên tiếp có tiến độ: 1", archive.read("progress_report.md").decode())
        modified = copy.deepcopy(self.payload)
        modified["tasks"][0]["title"] = "TẬP THỂ DỤC"
        modified["tasks"][0]["description"] = "Không được ghi đè"
        modified["tasks"][0]["weekdays"] = [0, 2, 4]
        preview = self.submit("preview", modified).get_data(as_text=True)
        self.assertIn("bỏ qua 2 việc", preview)
        self.submit(payload=modified)
        self.assertEqual(len(self.rows("SELECT * FROM habits")), 2)
        self.assertEqual(self.rows("SELECT * FROM habit_entries"), before)
        self.assertEqual(self.rows("SELECT description FROM habits ORDER BY id")[0]["description"], "Vận động nhẹ")
        exported = self.client.get(f"/plans/{habit['plan_id']}/habits.json")
        parsed = parse_habit_plan(exported.get_data(as_text=True))
        self.assertEqual(parsed["tasks"][0]["title"], "Tập thể dục")
        self.submit(payload=parsed)
        self.assertEqual(len(self.rows("SELECT * FROM habits")), 2)

    def test_existing_destination_keeps_metadata_and_supports_paused_plan(self):
        self.client.post("/plans", data={"name": "Kế hoạch đang có", "kind": "habit"})
        plan = self.rows("SELECT * FROM study_plans WHERE kind='habit'")[0]
        self.client.post(f"/plans/{plan['id']}", data={"name": plan["name"], "description": "Giữ nguyên",
                         "priority": "100", "status": "Tạm dừng"})
        preview = self.submit("preview", habit_plan_id=plan["id"]).get_data(as_text=True)
        self.assertIn("Kích hoạt lại", preview)
        self.submit(habit_plan_id=plan["id"])
        self.assertEqual(len(self.rows("SELECT * FROM study_plans WHERE kind='habit'")), 1)
        self.assertEqual(self.rows("SELECT description FROM study_plans WHERE id=?", (plan["id"],))[0]["description"], "Giữ nguyên")
        self.assertEqual(len(self.rows("SELECT * FROM habits WHERE plan_id=?", (plan["id"],))), 2)
        page = self.client.get(f"/import-plan?import_type=habit&habit_plan_id={plan['id']}").get_data(as_text=True)
        self.assertIn('value="habit" selected', page)
        self.assertIn(f'value="{plan["id"]}" selected', page)

    def test_import_and_kit_are_owner_only_even_with_sharing_and_admin_access(self):
        self.submit()
        plan = self.rows("SELECT id FROM study_plans WHERE kind='habit'")[0]["id"]
        with self.app.app_context():
            create_user("bob", "bob-password")
            get_db().commit()
        self.client.post(f"/plans/{plan}/share", data={"username": "bob"})
        self.client.post("/logout")
        self.client.post("/login", data={"username": "bob", "password": "bob-password"})
        before = self.rows("SELECT * FROM habits")
        for mode in ("preview", "confirm"):
            response = self.submit(mode, habit_plan_id=plan)
            self.assertIn("Chỉ nhập vào kế hoạch thói quen của chính bạn", response.get_data(as_text=True))
        self.assertEqual(self.rows("SELECT * FROM habits"), before)
        self.assertEqual(self.client.get(f"/plans/{plan}/habits.json").status_code, 404)
        self.assertEqual(self.client.get(f"/import-plan/habit-ai-kit.zip?plan_id={plan}").status_code, 404)
        with ZipFile(BytesIO(self.client.get("/import-plan/habit-ai-kit.zip").data)) as archive:
            context = json.loads(archive.read("ai-kit-thoi-quen/current_plans.json"))
            self.assertEqual(context["plans"], [])
        self.submit()  # Same name may create a separate plan owned by Bob.
        bob_plan = self.rows("SELECT id FROM study_plans WHERE kind='habit' AND id!=?", (plan,))[0]["id"]
        self.client.post("/logout")
        self.client.post("/login", data={"username": "admin", "password": "admin-password"})
        self.assertEqual(self.client.get(f"/plans/{bob_plan}/habits.json").status_code, 404)
        self.assertIn("Chỉ nhập vào", self.submit(habit_plan_id=bob_plan).get_data(as_text=True))
        # Shared tasks are visible on the calendar but excluded from own counts.
        self.client.post("/logout")
        self.client.post("/login", data={"username": "bob", "password": "bob-password"})
        page, context = self.context(f"/schedule?filter=1&plan_id={plan}")
        self.assertEqual(len(context["day_habits"]), 2)
        self.assertEqual(context["today_habits"], [])
        self.assertIn(f"/shared/plans/{plan}?date=2026-11-06", page)

    def test_kit_contains_valid_sample_schema_and_scoped_existing_tasks(self):
        self.submit()
        self.client.post("/plans", data={"name": "Kế hoạch riêng khác", "kind": "habit"})
        plan = self.rows("SELECT id FROM study_plans WHERE name='Sức khỏe'")[0]["id"]
        response = self.client.get(f"/import-plan/habit-ai-kit.zip?plan_id={plan}")
        self.assertEqual(response.status_code, 200)
        with ZipFile(BytesIO(response.data)) as archive:
            files = {Path(n).name: archive.read(n).decode("utf-8") for n in archive.namelist()}
        self.assertEqual(set(files), {"PROMPT.md", "habit_plan.schema.json", "sample_plan.json", "current_plans.json"})
        self.assertEqual(parse_habit_plan(files["sample_plan.json"])["tasks"][0]["start_date"], "2026-11-06")
        context = json.loads(files["current_plans.json"])
        self.assertEqual(context["today"], "2026-11-06")
        self.assertEqual([p["name"] for p in context["plans"]], ["Sức khỏe"])
        self.assertEqual(len(context["plans"][0]["tasks"]), 2)
        self.assertEqual(json.loads(files["habit_plan.schema.json"])["properties"]["plan_type"]["const"], "habit")

    def test_upload_utf8_bom_and_invalid_destination_do_not_change_study_plans(self):
        response = self.client.post("/import-plan", data={"mode": "confirm", "import_type": "habit",
            "plan_file": (BytesIO(json.dumps(self.payload, ensure_ascii=False).encode("utf-8-sig")), "tasks.json")})
        self.assertEqual(response.status_code, 302)
        before = self.rows("SELECT * FROM habits")
        study = self.rows("SELECT id FROM study_plans WHERE kind='study'")[0]["id"]
        for target in (str(study), "-1", "bad", "9" * 100):
            response = self.submit(habit_plan_id=target)
            self.assertEqual(response.status_code, 200)
            self.assertIn('class="notice error"', response.get_data(as_text=True))
        self.assertEqual(self.rows("SELECT * FROM habits"), before)

    def test_invalid_task_rejects_whole_file_before_any_write(self):
        variants = [{"title": ""}, {"weekdays": []}, {"weekdays": [True]}, {"weekdays": [1.0]},
                    {"weekdays": [0, 0]}, {"weekdays": [7]}, {"weekdays": ["4"]},
                    {"start_date": "2026-02-30"}, {"start_date": 20261106},
                    {"start_date": "20261106"}, {"end_date": ""}, {"end_date": "2026-11-01"},
                    {"weekdays": [0], "start_date": "2026-11-06", "end_date": "2026-11-06"},
                    {"status": "done"}, {"hours": 1}, {"description": "a" * 2001}]
        variants += [{"excluded_dates": value} for value in (
            None, "2026-11-06", [True], ["2026-02-30"], ["20261106"],
            ["2026-11-06", "2026-11-06"], ["2026-11-06"], ["2026-11-07"] * 367)]
        for changes in variants:
            with self.subTest(changes=changes):
                payload = copy.deepcopy(self.payload)
                payload["tasks"][1].update(changes)
                response = self.submit(payload=payload)
                self.assertEqual(response.status_code, 200)
                self.assertIn('class="notice error"', response.get_data(as_text=True))
                self.assertEqual(self.rows("SELECT * FROM habits"), [])
                self.assertEqual(self.rows("SELECT * FROM study_plans WHERE kind='habit'"), [])
        for invalid in ([], None, {}, {**self.payload, "tasks": []},
                        {**self.payload, "schema_version": True},
                        {**self.payload, "tasks": [self.payload["tasks"][0]] * 2}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_habit_plan(json.dumps(invalid))

    def test_exclusions_survive_import_export_and_do_not_count_or_break_habit_streak(self):
        payload = copy.deepcopy(self.payload)
        payload["tasks"] = [payload["tasks"][0]]
        payload["tasks"][0]["excluded_dates"] = ["2026-11-04"]
        preview = self.submit("preview", payload).get_data(as_text=True)
        self.assertIn("Ngày loại trừ (1)", preview)
        self.assertIn("2026-11-04", preview)
        self.assertEqual(self.rows("SELECT * FROM habits"), [])
        self.submit(payload=payload)
        habit = self.rows("SELECT * FROM habits")[0]
        for day in ("2026-11-02", "2026-11-06", "2026-11-04"):
            self.client.post(f"/habits/{habit['id']}/entries", data={"date": day, "status": "done"})
        self.assertEqual([r["entry_date"] for r in self.rows("SELECT * FROM habit_entries ORDER BY entry_date")],
                         ["2026-11-02", "2026-11-06"])
        page, plan = self.context(f"/plans/{habit['plan_id']}")
        self.assertIn("Ngày loại trừ (1)", page)
        self.assertEqual(plan["habits"][0]["streak"], 2)
        self.assertEqual(plan["week_stats"]["total"], 2)
        self.assertEqual(plan["week_stats"]["percent"], 100)
        _, schedule = self.context(f"/schedule?filter=1&plan_id={habit['plan_id']}&date=2026-11-04")
        self.assertEqual(schedule["day_habits"], [])
        self.assertNotIn("2026-11-04", schedule["habit_days"])
        exported = self.client.get(f"/plans/{habit['plan_id']}/habits.json").get_json()
        self.assertEqual(exported["tasks"][0]["excluded_dates"], ["2026-11-04"])
        self.submit(payload=exported)
        self.assertEqual(len(self.rows("SELECT * FROM habits")), 1)
        with ZipFile(BytesIO(self.client.get(f"/import-plan/habit-ai-kit.zip?plan_id={habit['plan_id']}").data)) as archive:
            context = json.loads(archive.read("ai-kit-thoi-quen/current_plans.json"))
            self.assertEqual(context["plans"][0]["tasks"][0]["excluded_dates"], ["2026-11-04"])
            self.assertIn("nguồn chính thức", archive.read("ai-kit-thoi-quen/PROMPT.md").decode())
        with ZipFile(BytesIO(self.client.post("/export").data)) as archive:
            data = json.loads(archive.read("learning_data.json"))
            self.assertEqual(json.loads(data["habits"][0]["excluded_dates_json"]), ["2026-11-04"])

    def test_changed_exclusions_reject_reimport_atomically_without_changing_history(self):
        self.submit()
        habit = self.rows("SELECT * FROM habits ORDER BY id")[0]
        self.client.post(f"/habits/{habit['id']}/entries", data={"date": "2026-11-06", "status": "done"})
        before = self.rows("SELECT * FROM habit_entries")
        payload = copy.deepcopy(self.payload)
        payload["tasks"][0]["excluded_dates"] = ["2026-11-06"]
        payload["tasks"].append({"title": "New task", "weekdays": [4], "start_date": "2026-11-06"})
        for mode in ("preview", "confirm"):
            response = self.submit(mode, payload)
            self.assertIn("khác ngày loại trừ", response.get_data(as_text=True))
        self.assertEqual(self.rows("SELECT * FROM habit_entries"), before)
        self.assertEqual(len(self.rows("SELECT * FROM habits")), 2)
        self.assertTrue(all(row["excluded_dates_json"] == "[]" for row in self.rows("SELECT * FROM habits")))


if __name__ == "__main__":
    unittest.main()
