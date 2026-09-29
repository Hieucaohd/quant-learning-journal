"""Export and replace one user's course inside a study plan."""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from .auth import current_user_id
from .db import get_db, sync_lecture_hours
from .scheduler import replan
from .timezone import local_today


SCHEMA_VERSION = 2
EXPORT_TYPE = "course_plan_snapshot"
STATUSES = ("Chưa bắt đầu", "Đang học", "Hoàn thành", "Cần ôn tập")
TASK_TYPES = ("video", "exercises", "review", "reading", "project", "other")


def _rows(sql, params=()):
    return [dict(row) for row in get_db().execute(sql, params)]


def course_snapshot(plan_id, course_id):
    """Return a complete, AI-friendly snapshot for an owned plan/course pair."""
    db = get_db()
    user_id = current_user_id()
    plan = db.execute("""SELECT p.*, pc.position FROM study_plans p
        JOIN plan_courses pc ON pc.plan_id=p.id
        WHERE p.id=? AND pc.course_id=? AND p.owner_user_id=?""",
                      (plan_id, course_id, user_id)).fetchone()
    course = db.execute("SELECT * FROM courses WHERE id=? AND owner_user_id=?",
                        (course_id, user_id)).fetchone()
    if plan is None or course is None:
        raise LookupError("Không tìm thấy khóa học trong kế hoạch này")

    course_data = dict(course)
    course_data["name"] = course["display_name"] or course["name"]
    for key in ("owner_user_id", "catalog_course_id", "display_name"):
        course_data.pop(key, None)
    course_data["source_course_id"] = course_data.pop("id")
    lectures = []
    for lecture in db.execute("""SELECT * FROM lectures WHERE course_id=?
        ORDER BY lecture_number, id""", (course_id,)):
        item = dict(lecture)
        lecture_id = item.pop("id")
        item.pop("course_id", None)
        item["source_lecture_id"] = lecture_id
        tasks = []
        for task in db.execute("""SELECT * FROM lecture_tasks WHERE lecture_id=?
            ORDER BY position, id""", (lecture_id,)):
            part = dict(task)
            part["source_task_id"] = part.pop("id")
            part.pop("lecture_id", None)
            part["type"] = part.pop("kind")
            part["completed_on_time"] = (None if part["completed_on_time"] is None
                                          else bool(part["completed_on_time"]))
            tasks.append(part)
        item["completed_on_time"] = (None if item["completed_on_time"] is None
                                      else bool(item["completed_on_time"]))
        item["number"] = item.pop("lecture_number")
        item["tasks"] = tasks
        lectures.append(item)
    course_data["lectures"] = lectures

    lecture_ids = [lecture["source_lecture_id"] for lecture in lectures]
    placeholders = ",".join("?" for _ in lecture_ids) or "NULL"
    return {
        "schema_version": SCHEMA_VERSION,
        "export_type": EXPORT_TYPE,
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ai_instructions": {
            "goal": "Điều chỉnh cấu trúc và khối lượng học, rồi trả lại toàn bộ JSON này.",
            "editable_fields": [
                "course.name", "course.description", "course.start_date", "course.notes",
                "course.lectures",
                "course.lectures[].title", "course.lectures[].content",
                "course.lectures[].understanding_score", "course.lectures[].notes",
                "course.lectures[].manual_deadline", "course.lectures[].tasks",
                "course.lectures[].tasks[].type", "course.lectures[].tasks[].title",
                "course.lectures[].tasks[].content",
                "course.lectures[].tasks[].estimated_hours",
            ],
            "progress_fields": [
                "status", "remaining_hours", "completed_at", "completed_on_time"
            ],
            "rules": [
                "Giữ nguyên schema_version, export_type, plan.source_plan_id và course.source_course_id.",
                "Có thể thêm, xóa hoặc đổi thứ tự bài học và phần việc.",
                "Bài/phần việc Hoàn thành phải có completed_at; phần việc Hoàn thành có remaining_hours bằng 0.",
                "Không sửa history và scheduling_context; các phần này chỉ cung cấp ngữ cảnh.",
                "Trả về đúng một đối tượng JSON, không bọc trong Markdown.",
            ],
        },
        "plan": {
            "source_plan_id": plan["id"], "name": plan["name"],
            "description": plan["description"], "status": plan["status"],
            "priority": plan["priority"], "course_position": plan["position"],
        },
        "course": course_data,
        "scheduling_context": {
            "capacity_profiles": _rows("""SELECT effective_from, hours_json, reason, created_at
                FROM user_capacity_profiles WHERE user_id=? ORDER BY effective_from""", (user_id,)),
            "capacity_overrides": _rows("""SELECT study_date, hours, reason, created_at
                FROM user_capacity_overrides WHERE user_id=? ORDER BY study_date""", (user_id,)),
            "allocations": _rows("""SELECT a.study_date, a.hours, l.lecture_number,
                t.position AS task_position, t.title AS task_title
                FROM schedule_allocations a LEFT JOIN lectures l ON l.id=a.lecture_id
                LEFT JOIN lecture_tasks t ON t.id=a.task_id WHERE a.course_id=?
                ORDER BY a.study_date, a.id""", (course_id,)),
        },
        "history": {
            "schedule_events": _rows("""SELECT changed_at, old_deadline, new_deadline,
                reason, kind, lecture_id, task_id FROM schedule_events
                WHERE course_id=? ORDER BY id""", (course_id,)),
            "missed_deadlines": _rows(f"""SELECT recorded_at, old_deadline, new_deadline,
                reason, lecture_id, task_id FROM missed_deadlines
                WHERE lecture_id IN ({placeholders}) ORDER BY id""", lecture_ids),
            "completion_events": _rows("""SELECT old_date, new_date, reason, recorded_at,
                lecture_id, task_id FROM completion_events WHERE course_id=? ORDER BY id""",
                                       (course_id,)),
            "journals": _rows("""SELECT date, topic, lecture, hours, difficulty,
                understanding, notes, problems, next_plan FROM journals
                WHERE course_id=? AND user_id=? ORDER BY date, id""", (course_id, user_id)),
        },
    }


def snapshot_json(plan_id, course_id):
    return json.dumps(course_snapshot(plan_id, course_id), ensure_ascii=False,
                      indent=2, default=str) + "\n"


def _object(value, path, required, optional=()):
    if not isinstance(value, dict):
        raise ValueError(f"{path} phải là một đối tượng JSON")
    missing = set(required) - value.keys()
    if missing:
        raise ValueError(f"{path} thiếu trường: {', '.join(sorted(missing))}")
    extra = value.keys() - set(required) - set(optional)
    if extra:
        raise ValueError(f"{path} có trường không hỗ trợ: {', '.join(sorted(extra))}")


def _string(value, path, maximum=3000, allow_empty=False):
    if not isinstance(value, str):
        raise ValueError(f"{path} phải là chuỗi ký tự")
    value = value.strip()
    if not value and not allow_empty:
        raise ValueError(f"{path} không được để trống")
    if len(value) > maximum:
        raise ValueError(f"{path} dài quá {maximum} ký tự")
    return value


def _number(value, path, minimum=0, maximum=10000):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} phải là một số")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{path} không hợp lệ") from exc
    if not number.is_finite() or not Decimal(str(minimum)) <= number <= Decimal(str(maximum)):
        raise ValueError(f"{path} phải từ {minimum} đến {maximum}")
    if number.as_tuple().exponent < -2:
        raise ValueError(f"{path} chỉ được có tối đa hai chữ số thập phân")
    return float(number)


def _integer(value, path, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{path} phải là số nguyên từ {minimum} đến {maximum}")
    return value


def _date(value, path, allow_none=True):
    if value is None and allow_none:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{path} phải là ngày YYYY-MM-DD hoặc null")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"{path} phải là ngày hợp lệ theo định dạng YYYY-MM-DD") from exc


def _nullable_bool(value, path):
    if value is not None and type(value) is not bool:
        raise ValueError(f"{path} phải là true, false hoặc null")
    return value


def _status(value, path):
    value = _string(value, path, 30)
    if value not in STATUSES:
        raise ValueError(f"{path} không hợp lệ: {value}")
    return value


def parse_course_snapshot(raw):
    """Validate schema v2 and return only fields that may be written back."""
    if len(raw.encode("utf-8")) > 5 * 1024 * 1024:
        raise ValueError("Snapshot JSON vượt quá 5 MB")
    try:
        source = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON không hợp lệ tại dòng {exc.lineno}, cột {exc.colno}") from exc
    _object(source, "Gốc", ("schema_version", "export_type", "plan", "course"),
            ("exported_at", "ai_instructions", "scheduling_context", "history"))
    if source["schema_version"] != SCHEMA_VERSION or source["export_type"] != EXPORT_TYPE:
        raise ValueError("File phải là snapshot khóa học phiên bản 2 do ứng dụng xuất ra")

    plan = source["plan"]
    _object(plan, "plan", ("source_plan_id", "name"),
            ("description", "status", "priority", "course_position"))
    plan_id = _integer(plan["source_plan_id"], "plan.source_plan_id", 1, 2_147_483_647)

    course = source["course"]
    _object(course, "course", ("source_course_id", "name", "lectures"),
            ("description", "start_date", "target_date", "status", "progress", "notes",
             "estimated_hours"))
    course_id = _integer(course["source_course_id"], "course.source_course_id", 1, 2_147_483_647)
    lectures_source = course["lectures"]
    if not isinstance(lectures_source, list) or len(lectures_source) > 10000:
        raise ValueError("course.lectures phải là danh sách có tối đa 10.000 bài học")

    lectures, seen_numbers, total_hours, task_count = [], set(), 0.0, 0
    for li, lecture in enumerate(lectures_source, 1):
        path = f"course.lectures[{li}]"
        _object(lecture, path, ("number", "title", "tasks"),
                ("source_lecture_id", "content", "status", "estimated_hours",
                 "remaining_hours", "understanding_score", "notes", "deadline",
                 "manual_deadline", "completed_at", "completed_on_time"))
        number = _integer(lecture["number"], f"{path}.number", 1, 10000)
        if number in seen_numbers:
            raise ValueError(f"Bài {number} xuất hiện nhiều lần")
        seen_numbers.add(number)
        tasks_source = lecture["tasks"]
        if not isinstance(tasks_source, list) or len(tasks_source) > 100:
            raise ValueError(f"{path}.tasks phải là danh sách có tối đa 100 phần việc")
        tasks, seen_positions = [], set()
        for ti, task in enumerate(tasks_source, 1):
            tpath = f"{path}.tasks[{ti}]"
            _object(task, tpath, ("position", "type", "title", "estimated_hours"),
                    ("source_task_id", "content", "remaining_hours", "status", "deadline",
                     "manual_deadline", "completed_at", "completed_on_time"))
            position = _integer(task["position"], f"{tpath}.position", 1, 100)
            if position in seen_positions:
                raise ValueError(f"{path} có vị trí phần việc {position} xuất hiện nhiều lần")
            seen_positions.add(position)
            kind = _string(task["type"], f"{tpath}.type", 30)
            if kind not in TASK_TYPES:
                raise ValueError(f"{tpath}.type không hợp lệ: {kind}")
            estimated = _number(task["estimated_hours"], f"{tpath}.estimated_hours", .01, 100)
            status = _status(task.get("status", "Chưa bắt đầu"), f"{tpath}.status")
            completed_at = _date(task.get("completed_at"), f"{tpath}.completed_at")
            if status == "Hoàn thành" and completed_at is None:
                raise ValueError(f"{tpath}.completed_at là bắt buộc khi phần việc đã hoàn thành")
            if status != "Hoàn thành" and completed_at is not None:
                raise ValueError(f"{tpath}.status phải là Hoàn thành khi có completed_at")
            remaining = _number(task.get("remaining_hours", estimated),
                                f"{tpath}.remaining_hours", 0, estimated)
            if status == "Hoàn thành":
                remaining = 0.0
            elif remaining == 0:
                raise ValueError(f"{tpath}.remaining_hours phải lớn hơn 0 khi chưa hoàn thành")
            tasks.append({
                "position": position, "type": kind,
                "title": _string(task["title"], f"{tpath}.title", 200),
                "content": _string(task.get("content", ""), f"{tpath}.content", 3000, True),
                "estimated_hours": estimated, "remaining_hours": remaining, "status": status,
                "deadline": _date(task.get("deadline"), f"{tpath}.deadline"),
                "manual_deadline": _date(task.get("manual_deadline"), f"{tpath}.manual_deadline"),
                "completed_at": completed_at,
                "completed_on_time": _nullable_bool(task.get("completed_on_time"),
                                                     f"{tpath}.completed_on_time"),
            })
            total_hours += estimated
            task_count += 1

        status = _status(lecture.get("status", "Chưa bắt đầu"), f"{path}.status")
        completed_at = _date(lecture.get("completed_at"), f"{path}.completed_at")
        if status == "Hoàn thành" and completed_at is None:
            raise ValueError(f"{path}.completed_at là bắt buộc khi bài học đã hoàn thành")
        if status != "Hoàn thành" and completed_at is not None:
            raise ValueError(f"{path}.status phải là Hoàn thành khi có completed_at")
        if tasks and status == "Hoàn thành" and any(t["status"] != "Hoàn thành" for t in tasks):
            raise ValueError(f"{path} đã hoàn thành nhưng vẫn có phần việc chưa hoàn thành")
        if tasks and all(t["status"] == "Hoàn thành" for t in tasks):
            status = "Hoàn thành"
            completed_at = completed_at or max(t["completed_at"] for t in tasks)
        lecture_estimated = (round(sum(t["estimated_hours"] for t in tasks), 2) if tasks else
                             _number(lecture.get("estimated_hours", 0),
                                     f"{path}.estimated_hours", 0, 10000))
        lecture_remaining = (round(sum(t["remaining_hours"] for t in tasks), 2) if tasks else
                             _number(lecture.get("remaining_hours", lecture_estimated),
                                     f"{path}.remaining_hours", 0, lecture_estimated or 0))
        if status == "Hoàn thành":
            lecture_remaining = 0.0
        elif lecture_estimated and lecture_remaining == 0:
            raise ValueError(f"{path}.remaining_hours phải lớn hơn 0 khi chưa hoàn thành")
        if not tasks:
            total_hours += lecture_estimated
        score = lecture.get("understanding_score")
        if score is not None:
            score = _integer(score, f"{path}.understanding_score", 1, 10)
        lectures.append({
            "number": number, "title": _string(lecture["title"], f"{path}.title", 200),
            "content": _string(lecture.get("content", ""), f"{path}.content", 3000, True),
            "status": status, "estimated_hours": lecture_estimated,
            "remaining_hours": lecture_remaining, "understanding_score": score,
            "notes": _string(lecture.get("notes", ""), f"{path}.notes", 3000, True),
            "deadline": _date(lecture.get("deadline"), f"{path}.deadline"),
            "manual_deadline": _date(lecture.get("manual_deadline"), f"{path}.manual_deadline"),
            "completed_at": completed_at,
            "completed_on_time": _nullable_bool(lecture.get("completed_on_time"),
                                                 f"{path}.completed_on_time"),
            "tasks": sorted(tasks, key=lambda item: item["position"]),
        })
    if total_hours > 10000:
        raise ValueError("Tổng thời lượng khóa học vượt quá 10.000 giờ")
    return {
        "schema_version": SCHEMA_VERSION, "export_type": EXPORT_TYPE,
        "plan_id": plan_id, "course_id": course_id,
        "course": {
            "name": _string(course["name"], "course.name", 200),
            "description": _string(course.get("description", ""), "course.description", 3000, True),
            "start_date": _date(course.get("start_date"), "course.start_date"),
            "notes": _string(course.get("notes", ""), "course.notes", 10000, True),
            "lectures": sorted(lectures, key=lambda item: item["number"]),
        },
        "lecture_count": len(lectures), "task_count": task_count,
        "total_hours": round(total_hours, 2),
    }


def inspect_course_overwrite(snapshot, selected_plan_id):
    db = get_db()
    user_id = current_user_id()
    if snapshot["plan_id"] != selected_plan_id:
        raise ValueError("Kế hoạch đã chọn không khớp source_plan_id trong file JSON")
    plan = db.execute("SELECT * FROM study_plans WHERE id=? AND owner_user_id=?",
                      (selected_plan_id, user_id)).fetchone()
    if plan is None:
        raise ValueError("Không tìm thấy kế hoạch của bạn")
    if plan["status"] != "Đang hoạt động":
        raise ValueError("Chỉ có thể ghi đè khóa học trong kế hoạch đang hoạt động")
    course = db.execute("""SELECT c.* FROM courses c JOIN plan_courses pc ON pc.course_id=c.id
        WHERE c.id=? AND c.owner_user_id=? AND pc.plan_id=?""",
                        (snapshot["course_id"], user_id, selected_plan_id)).fetchone()
    if course is None:
        raise ValueError("Khóa học nguồn không còn nằm trong kế hoạch đã chọn")
    affected = _rows("""SELECT p.id, p.name, p.status FROM study_plans p
        JOIN plan_courses pc ON pc.plan_id=p.id WHERE pc.course_id=? AND p.owner_user_id=?
        ORDER BY p.priority, p.id""", (course["id"], user_id))
    completed_lectures = sum(l["status"] == "Hoàn thành" for l in snapshot["course"]["lectures"])
    completed_tasks = sum(t["status"] == "Hoàn thành"
                          for l in snapshot["course"]["lectures"] for t in l["tasks"])
    return {
        "target_plan_name": plan["name"],
        "target_course_name": course["display_name"] or course["name"],
        "new_course_name": snapshot["course"]["name"],
        "affected_plans": affected, "completed_lectures": completed_lectures,
        "completed_tasks": completed_tasks,
    }


def _validated_target_plans(snapshot, source_plan_id, target_plan_ids):
    """Validate the plans chosen in preview and return all course memberships."""
    preview = inspect_course_overwrite(snapshot, source_plan_id)
    affected_ids = {item["id"] for item in preview["affected_plans"]}
    selected_ids = set(target_plan_ids)
    if not selected_ids or not selected_ids <= affected_ids:
        raise ValueError("Hãy chọn ít nhất một kế hoạch hợp lệ để áp dụng bản ghi đè")
    inactive = [item["name"] for item in preview["affected_plans"]
                if item["id"] in selected_ids and item["status"] != "Đang hoạt động"]
    if inactive:
        raise ValueError("Chỉ có thể áp dụng vào kế hoạch đang hoạt động: " + ", ".join(inactive))
    return preview, affected_ids, selected_ids


def _new_internal_course_name(display_name, user_id):
    db = get_db()
    if not db.execute("SELECT 1 FROM courses WHERE name=?", (display_name,)).fetchone():
        return display_name
    return f"{display_name} [u{user_id}-{uuid4().hex[:8]}]"


def apply_course_overwrite(snapshot, selected_plan_id, target_plan_ids, raw, source_name=""):
    """Replace course structure/state, preserve journals, then rebuild the schedule."""
    db = get_db()
    user_id = current_user_id()
    counts, affected_ids, selected_ids = _validated_target_plans(
        snapshot, selected_plan_id, target_plan_ids)
    source_course_id = snapshot["course_id"]
    source_course = db.execute("SELECT * FROM courses WHERE id=? AND owner_user_id=?",
                               (source_course_id, user_id)).fetchone()
    replace_in_place = selected_ids == affected_ids
    if replace_in_place:
        course_id = source_course_id
    else:
        course_id = db.execute("""INSERT INTO courses
            (name, display_name, description, start_date, target_date, status, progress,
             notes, estimated_hours, owner_user_id, catalog_course_id)
            VALUES (?, ?, ?, ?, NULL, 'Chưa bắt đầu', 0, ?, ?, ?, ?)""",
            (_new_internal_course_name(snapshot["course"]["name"], user_id),
             snapshot["course"]["name"], snapshot["course"]["description"],
             snapshot["course"]["start_date"], snapshot["course"]["notes"],
             snapshot["total_hours"], user_id, source_course["catalog_course_id"])).lastrowid
        for plan_id in selected_ids:
            db.execute("""UPDATE plan_courses SET course_id=?
                WHERE plan_id=? AND course_id=?""", (course_id, plan_id, source_course_id))

    lecture_ids = [row["id"] for row in db.execute(
        "SELECT id FROM lectures WHERE course_id=?", (course_id,))]
    placeholders = ",".join("?" for _ in lecture_ids) or "NULL"

    # Explicit child deletion also works on remote databases without FK cascades.
    db.execute("DELETE FROM schedule_allocations WHERE course_id=?", (course_id,))
    db.execute("DELETE FROM schedule_events WHERE course_id=?", (course_id,))
    db.execute("DELETE FROM completion_events WHERE course_id=?", (course_id,))
    db.execute(f"DELETE FROM missed_deadlines WHERE lecture_id IN ({placeholders})", lecture_ids)
    db.execute(f"DELETE FROM lecture_tasks WHERE lecture_id IN ({placeholders})", lecture_ids)
    db.execute("DELETE FROM lectures WHERE course_id=?", (course_id,))

    data = snapshot["course"]
    db.execute("""UPDATE courses SET display_name=?, description=?, start_date=?, target_date=NULL,
        notes=?, estimated_hours=?, status='Chưa bắt đầu', progress=0 WHERE id=?""",
               (data["name"], data["description"], data["start_date"], data["notes"],
                snapshot["total_hours"], course_id))
    inserted_lectures = []
    for lecture in data["lectures"]:
        lecture_id = db.execute("""INSERT INTO lectures
            (course_id, lecture_number, title, status, estimated_hours, remaining_hours,
             understanding_score, notes, content, deadline, manual_deadline, completed_at,
             completed_on_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (course_id, lecture["number"], lecture["title"], lecture["status"],
             lecture["estimated_hours"], lecture["remaining_hours"],
             lecture["understanding_score"], lecture["notes"], lecture["content"],
             lecture["deadline"] if lecture["status"] == "Hoàn thành" else None,
             lecture["manual_deadline"], lecture["completed_at"],
             None if lecture["completed_on_time"] is None else int(lecture["completed_on_time"]))).lastrowid
        inserted_lectures.append((lecture_id, lecture))
        for task in lecture["tasks"]:
            task_id = db.execute("""INSERT INTO lecture_tasks
                (lecture_id, position, kind, title, content, estimated_hours, remaining_hours,
                 status, deadline, manual_deadline, completed_at, completed_on_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (lecture_id, task["position"], task["type"], task["title"], task["content"],
                 task["estimated_hours"], task["remaining_hours"], task["status"],
                 task["deadline"] if task["status"] == "Hoàn thành" else None,
                 task["manual_deadline"], task["completed_at"],
                 None if task["completed_on_time"] is None else int(task["completed_on_time"]))).lastrowid
            if task["completed_at"]:
                db.execute("""INSERT INTO completion_events
                    (course_id, lecture_id, task_id, new_date, reason)
                    VALUES (?, ?, ?, ?, ?)""",
                    (course_id, lecture_id, task_id, task["completed_at"],
                     "Khôi phục trạng thái từ snapshot ghi đè"))
        if lecture["tasks"]:
            sync_lecture_hours(db, lecture_id)
        if lecture["completed_at"]:
            db.execute("""INSERT INTO completion_events
                (course_id, lecture_id, new_date, reason) VALUES (?, ?, ?, ?)""",
                (course_id, lecture_id, lecture["completed_at"],
                 "Khôi phục trạng thái từ snapshot ghi đè"))

    total = snapshot["total_hours"]
    remaining = sum(lecture["remaining_hours"] for _, lecture in inserted_lectures)
    statuses = [lecture["status"] for _, lecture in inserted_lectures]
    if statuses and all(status == "Hoàn thành" for status in statuses):
        course_status = "Hoàn thành"
    elif any(status in ("Đang học", "Hoàn thành", "Cần ôn tập") for status in statuses):
        course_status = "Đang học"
    else:
        course_status = "Chưa bắt đầu"
    progress = (100 if course_status == "Hoàn thành" else
                round((total - remaining) / total * 100) if total else 0)
    db.execute("UPDATE courses SET status=?, progress=? WHERE id=?",
               (course_status, progress, course_id))

    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    db.execute("""INSERT INTO plan_imports
        (source_name, payload_sha256, course_count, lecture_count, task_count, user_id)
        VALUES (?, ?, 1, ?, ?, ?)""",
        (source_name or "snapshot ghi đè", digest, snapshot["lecture_count"],
         snapshot["task_count"], user_id))
    replan(local_today(),
           f"Ghi đè khóa học từ snapshot: {source_name or data['name']}",
           preserve_overdue=True, recalculate_overdue_course_id=course_id)
    selected_names = [item["name"] for item in counts["affected_plans"]
                      if item["id"] in selected_ids]
    db.execute("""INSERT INTO schedule_events
        (course_id, lecture_id, reason, kind) VALUES (?, NULL, ?, 'ghi đè')""",
        (course_id, "Nhập snapshot điều chỉnh vào kế hoạch: " + ", ".join(selected_names)))
    counts["result_course_id"] = course_id
    counts["selected_plan_count"] = len(selected_ids)
    counts["created_separate_copy"] = not replace_in_place
    return counts
