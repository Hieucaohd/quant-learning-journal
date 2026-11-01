"""AI-assisted creation of recurring tasks, without changing check-in history."""
import json
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from flask import abort, request, send_file

from .auth import current_user_id
from .db import get_db
from .habits import WEEKDAYS, habits_for_plans
from .timezone import local_today


STATIC_DIR = Path(__file__).parent / "static"


def _text(value, label, limit, required=False):
    if not isinstance(value, str) or len(value.strip()) > limit or (required and not value.strip()):
        raise ValueError(f"{label} phải là chuỗi {'không rỗng, ' if required else ''}tối đa {limit} ký tự")
    return value.strip()


def _day(value, label):
    try:
        day = date.fromisoformat(value)
        if day.isoformat() != value or not 1901 <= day.year <= 9998:
            raise ValueError
        return day
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label} phải là ngày YYYY-MM-DD hợp lệ (1901–9998)") from exc


def _fields(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f"{label} không đúng định dạng; chỉ dùng các trường: {', '.join(allowed)}")


def task_key(task):
    return (task["title"].casefold(), tuple(task["weekdays"]), task["start_date"], task["end_date"])


def parse_habit_plan(raw):
    if len(raw.encode("utf-8")) > 1_000_000:
        raise ValueError("File thói quen phải nhỏ hơn 1 MB")
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise ValueError("Nội dung không phải JSON hợp lệ") from exc
    _fields(data, ("schema_version", "plan_type", "name", "description", "tasks"), "Kế hoạch")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1 or data.get("plan_type") != "habit":
        raise ValueError('Kế hoạch thói quen cần schema_version: 1 và plan_type: "habit"')
    result = {"schema_version": 1, "plan_type": "habit",
              "name": _text(data.get("name"), "Tên kế hoạch", 200, True),
              "description": _text(data.get("description", ""), "Mô tả kế hoạch", 2000), "tasks": []}
    if not isinstance(data.get("tasks"), list) or not 1 <= len(data["tasks"]) <= 365:
        raise ValueError("Kế hoạch cần từ 1 đến 365 phần việc")
    seen = set()
    for index, item in enumerate(data["tasks"], 1):
        label = f"Phần việc {index}"
        _fields(item, ("title", "description", "weekdays", "start_date", "end_date", "excluded_dates"), label)
        days = item.get("weekdays")
        if (not isinstance(days, list) or not 1 <= len(days) <= 7
                or any(type(d) is not int or not 0 <= d <= 6 for d in days)
                or len(set(days)) != len(days)):
            raise ValueError(f"{label}: weekdays là danh sách không trùng, từ 0 (Thứ 2) đến 6 (Chủ nhật)")
        start = _day(item.get("start_date"), f"{label}: ngày bắt đầu")
        end = _day(item["end_date"], f"{label}: ngày kết thúc") if item.get("end_date") is not None else None
        excluded = item.get("excluded_dates", [])
        if not isinstance(excluded, list) or len(excluded) > 366:
            raise ValueError(f"{label}: excluded_dates phải là danh sách tối đa 366 ngày")
        excluded = [_day(value, f"{label}: ngày loại trừ").isoformat() for value in excluded]
        if len(set(excluded)) != len(excluded):
            raise ValueError(f"{label}: danh sách ngày loại trừ không được trùng")
        if end and (end < start or not any((start + timedelta(days=i)).weekday() in days
                                          and (start + timedelta(days=i)).isoformat() not in excluded
                                          for i in range(min((end-start).days + 1, 7 * (len(excluded) + 1))))):
            raise ValueError(f"{label}: cần ít nhất một buổi đúng lịch lặp sau khi loại ngày nghỉ")
        task = {"title": _text(item.get("title"), f"{label}: tên việc", 200, True),
                "description": _text(item.get("description", ""), f"{label}: mô tả", 2000),
                "weekdays": sorted(days), "start_date": start.isoformat(),
                "end_date": end.isoformat() if end else None, "excluded_dates": sorted(excluded)}
        if task_key(task) in seen:
            raise ValueError(f"{label}: trùng tên và lịch lặp với phần việc khác trong file")
        seen.add(task_key(task))
        result["tasks"].append(task)
    return result


def inspect_habit_import(plan, plan_id=None):
    db = get_db()
    if plan_id:
        try:
            plan_id = int(plan_id)
            if not 0 < plan_id < 2**63:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValueError("Kế hoạch đích không hợp lệ") from exc
        target = db.execute("SELECT * FROM study_plans WHERE id=? AND owner_user_id=? AND kind='habit'",
                            (plan_id, current_user_id())).fetchone()
        if target is None:
            raise ValueError("Chỉ nhập vào kế hoạch thói quen của chính bạn")
    else:
        target = db.execute("SELECT * FROM study_plans WHERE name=? AND owner_user_id=?",
                            (plan["name"], current_user_id())).fetchone()
        if target and target["kind"] != "habit":
            raise ValueError("Tên này đã dùng cho kế hoạch theo giờ. Hãy đổi tên trong JSON hoặc chọn kế hoạch thói quen đích")
    existing = {}
    for habit in habits_for_plans([target["id"]]) if target else []:
        existing.setdefault(task_key(habit), []).append(habit)
    for task in plan["tasks"]:
        if any(h["excluded_dates"] != task["excluded_dates"] for h in existing.get(task_key(task), [])):
            raise ValueError(f"Việc '{task['title']}' đã có nhưng khác ngày loại trừ. Import chỉ thêm việc; "
                             "hãy kết thúc việc cũ và tạo việc mới từ ngày áp dụng để giữ lịch sử")
    tasks = [dict(t, exists=task_key(t) in existing,
                  repeat_label=", ".join(WEEKDAYS[d] for d in t["weekdays"])) for t in plan["tasks"]]
    new_count = sum(not t["exists"] for t in tasks)
    return {"tasks": tasks, "new_tasks": new_count, "existing_tasks": len(tasks)-new_count,
            "target_id": target["id"] if target else None,
            "target_name": target["name"] if target else plan["name"],
            "target_status": target["status"] if target else "Đang hoạt động"}


def apply_habit_import(plan, plan_id=None):
    """Re-check ownership and duplicates at confirmation; caller commits atomically."""
    result = inspect_habit_import(plan, plan_id)
    db = get_db()
    target_id = result["target_id"]
    if target_id is None:
        target_id = db.execute("""INSERT INTO study_plans (name, description, owner_user_id, kind)
            VALUES (?, ?, ?, 'habit')""", (plan["name"], plan["description"], current_user_id())).lastrowid
    for task in result["tasks"]:
        if not task["exists"]:
            db.execute("""INSERT INTO habits (plan_id, title, description, weekdays_json, start_date, end_date,
                excluded_dates_json) VALUES (?, ?, ?, ?, ?, ?, ?)""", (target_id, task["title"], task["description"],
                json.dumps(task["weekdays"]), task["start_date"], task["end_date"], json.dumps(task["excluded_dates"])))
    return dict(result, target_id=target_id)


def owned_plan(plan_id):
    plan = get_db().execute("SELECT * FROM study_plans WHERE id=? AND owner_user_id=? AND kind='habit'",
                            (plan_id, current_user_id())).fetchone()
    if plan is None:
        abort(404)
    return plan


def plan_payload(plan):
    return {"schema_version": 1, "plan_type": "habit", "name": plan["name"],
            "description": plan["description"],
            "tasks": [{key: h[key] for key in ("title", "description", "weekdays", "start_date", "end_date", "excluded_dates")}
                      for h in habits_for_plans([plan["id"]])]}


def _json(data):
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def download_habit_plan(plan_id):
    payload = plan_payload(owned_plan(plan_id))
    return send_file(BytesIO(_json(payload).encode("utf-8")), mimetype="application/json",
                     as_attachment=True, download_name=f"ke-hoach-thoi-quen-{plan_id}.json")


def download_habit_ai_kit():
    plan_id = request.args.get("plan_id")
    if plan_id:
        try:
            plan_id = int(plan_id)
            if not 0 < plan_id < 2**63:
                raise ValueError
        except ValueError:
            abort(400)
        plans = [owned_plan(plan_id)]
    else:
        plans = get_db().execute("SELECT * FROM study_plans WHERE owner_user_id=? AND kind='habit' ORDER BY id",
                                 (current_user_id(),)).fetchall()
    today = local_today()
    context = {"today": today.isoformat(), "timezone": "Asia/Ho_Chi_Minh",
               "plans": [dict(plan_payload(p), status=p["status"]) for p in plans]}
    sample = {"schema_version": 1, "plan_type": "habit", "name": "Vận động mỗi ngày", "description": "",
              "tasks": [{"title": "Tập thể dục", "description": "Vận động theo mục tiêu cá nhân",
                         "weekdays": list(range(7)), "start_date": today.isoformat(), "end_date": None,
                         "excluded_dates": []}]}
    files = {"PROMPT.md": (STATIC_DIR / "AI_HABIT_PROMPT.md").read_text(encoding="utf-8"),
             "habit_plan.schema.json": (STATIC_DIR / "habit_plan.schema.json").read_text(encoding="utf-8"),
             "sample_plan.json": _json(sample), "current_plans.json": _json(context)}
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(f"ai-kit-thoi-quen/{name}", content)
    output.seek(0)
    return send_file(output, mimetype="application/zip", as_attachment=True,
                     download_name=f"ai-kit-thoi-quen-{today.isoformat()}.zip")


def register_routes(bp):
    bp.add_url_rule("/import-plan/habit-ai-kit.zip", "download_habit_ai_kit", download_habit_ai_kit)
    bp.add_url_rule("/plans/<int:plan_id>/habits.json", "download_habit_plan", download_habit_plan)
