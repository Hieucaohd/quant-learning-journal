import hashlib
import json
from decimal import Decimal, InvalidOperation

from .db import get_db, sync_lecture_hours
from .scheduler import replan


TASK_TYPES = {
    "video": "Xem video",
    "exercises": "Làm bài tập",
    "review": "Ôn luyện",
    "reading": "Đọc tài liệu",
    "project": "Dự án/thực hành",
    "other": "Khác",
}


def _object(value, path, required, optional=()):
    if not isinstance(value, dict):
        raise ValueError(f"{path} phải là một đối tượng JSON")
    missing = set(required) - value.keys()
    extra = value.keys() - set(required) - set(optional)
    if missing:
        raise ValueError(f"{path} thiếu trường: {', '.join(sorted(missing))}")
    if extra:
        raise ValueError(f"{path} có trường không hỗ trợ: {', '.join(sorted(extra))}")


def _string(value, path, max_length=3000, required=True):
    if not isinstance(value, str):
        raise ValueError(f"{path} phải là chuỗi ký tự")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{path} không được để trống")
    if len(value) > max_length:
        raise ValueError(f"{path} dài quá {max_length} ký tự")
    return value


def _hours(value, path):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} phải là số giờ")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{path} phải là số giờ hợp lệ") from exc
    if not number.is_finite() or not Decimal("0.01") <= number <= Decimal("100"):
        raise ValueError(f"{path} phải từ 0,01 đến 100 giờ")
    if number.as_tuple().exponent < -2:
        raise ValueError(f"{path} chỉ được có tối đa hai chữ số thập phân")
    return float(number)


def parse_plan(raw):
    if len(raw.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Kế hoạch JSON vượt quá 1 MB")
    try:
        source = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON không hợp lệ tại dòng {exc.lineno}, cột {exc.colno}") from exc
    _object(source, "Gốc", ("schema_version", "courses"))
    if type(source["schema_version"]) is not int or source["schema_version"] != 1:
        raise ValueError("schema_version phải bằng 1")
    if not isinstance(source["courses"], list) or not source["courses"]:
        raise ValueError("courses phải là danh sách có ít nhất một khóa học")
    if len(source["courses"]) > 100:
        raise ValueError("Kế hoạch có quá nhiều khóa học")
    seen_courses = set()
    normalized = []
    lecture_count = task_count = 0
    total_hours = 0.0
    for ci, course in enumerate(source["courses"], 1):
        path = f"courses[{ci}]"
        _object(course, path, ("name", "lectures"), ("description",))
        name = _string(course["name"], f"{path}.name", 200)
        if name.casefold() in seen_courses:
            raise ValueError(f"Khóa học {name} xuất hiện nhiều lần")
        seen_courses.add(name.casefold())
        description = _string(course.get("description", ""), f"{path}.description", 1000, False)
        lecture_list = course["lectures"]
        if not isinstance(lecture_list, list) or not lecture_list:
            raise ValueError(f"{path}.lectures phải có ít nhất một bài học")
        seen_numbers = set()
        lectures = []
        for li, lecture in enumerate(lecture_list, 1):
            lpath = f"{path}.lectures[{li}]"
            _object(lecture, lpath, ("number", "title", "tasks"), ("content",))
            number = lecture["number"]
            if type(number) is not int or number < 1 or number > 10000:
                raise ValueError(f"{lpath}.number phải là số nguyên dương")
            if number in seen_numbers:
                raise ValueError(f"Khóa {name} có bài {number} xuất hiện nhiều lần")
            seen_numbers.add(number)
            title = _string(lecture["title"], f"{lpath}.title", 200)
            content = _string(lecture.get("content", ""), f"{lpath}.content", required=False)
            task_list = lecture["tasks"]
            if not isinstance(task_list, list) or not 1 <= len(task_list) <= 100:
                raise ValueError(f"{lpath}.tasks phải có từ 1 đến 100 phần việc")
            tasks = []
            for ti, task in enumerate(task_list, 1):
                tpath = f"{lpath}.tasks[{ti}]"
                _object(task, tpath, ("type", "title", "hours"), ("content",))
                kind = _string(task["type"], f"{tpath}.type", 30)
                if kind not in TASK_TYPES:
                    raise ValueError(f"{tpath}.type không hợp lệ: {kind}")
                task_title = _string(task["title"], f"{tpath}.title", 200)
                task_content = _string(task.get("content", ""), f"{tpath}.content",
                                       required=False)
                hours = _hours(task["hours"], f"{tpath}.hours")
                total_hours += hours
                task_count += 1
                tasks.append({"type": kind, "title": task_title,
                              "content": task_content, "hours": hours})
            lecture_count += 1
            lectures.append({"number": number, "title": title,
                             "content": content, "tasks": tasks})
        normalized.append({"name": name, "description": description, "lectures": lectures})
    if total_hours > 10000:
        raise ValueError("Tổng kế hoạch vượt quá 10.000 giờ")
    return {"schema_version": 1, "courses": normalized,
            "course_count": len(normalized), "lecture_count": lecture_count,
            "task_count": task_count, "total_hours": round(total_hours, 2)}


def inspect_plan(plan):
    db = get_db()
    new_courses = new_lectures = existing_lectures = 0
    for course in plan["courses"]:
        existing_course = db.execute("SELECT * FROM courses WHERE name=?", (course["name"],)).fetchone()
        if existing_course is None:
            new_courses += 1
            new_lectures += len(course["lectures"])
            continue
        for lecture in course["lectures"]:
            existing = db.execute("""SELECT * FROM lectures WHERE course_id=?
                AND lecture_number=?""", (existing_course["id"], lecture["number"])).fetchone()
            if existing is None:
                new_lectures += 1
                continue
            if existing["status"] == "Hoàn thành":
                raise ValueError(f"{course['name']} · Bài {lecture['number']} đã hoàn thành. "
                                 "Hãy chỉ đưa các bài chưa hoàn thành vào JSON.")
            if db.execute("SELECT 1 FROM lecture_tasks WHERE lecture_id=?", (existing["id"],)).fetchone():
                raise ValueError(f"{course['name']} · Bài {lecture['number']} đã có phần việc. "
                                 "Hãy nhập những bài chưa có phần việc để tránh ghi đè tiến độ.")
            existing_lectures += 1
    return {"new_courses": new_courses, "new_lectures": new_lectures,
            "existing_lectures": existing_lectures}


def apply_plan(plan, raw, source_name=""):
    db = get_db()
    counts = inspect_plan(plan)
    for course in plan["courses"]:
        row = db.execute("SELECT * FROM courses WHERE name=?", (course["name"],)).fetchone()
        if row is None:
            cursor = db.execute("INSERT INTO courses (name, description) VALUES (?, ?)",
                                (course["name"], course["description"]))
            course_id = cursor.lastrowid
        else:
            course_id = row["id"]
            if course["description"] and not row["description"]:
                db.execute("UPDATE courses SET description=? WHERE id=?",
                           (course["description"], course_id))
        for lecture in course["lectures"]:
            row = db.execute("""SELECT * FROM lectures WHERE course_id=? AND lecture_number=?""",
                             (course_id, lecture["number"])).fetchone()
            hours = round(sum(task["hours"] for task in lecture["tasks"]), 2)
            if row is None:
                cursor = db.execute("""INSERT INTO lectures
                    (course_id, lecture_number, title, content, estimated_hours, remaining_hours)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (course_id, lecture["number"], lecture["title"], lecture["content"],
                     hours, hours))
                lecture_id = cursor.lastrowid
                db.execute("""UPDATE courses SET status='Đang học'
                    WHERE id=? AND status='Hoàn thành'""", (course_id,))
            else:
                lecture_id = row["id"]
                title = (lecture["title"] if row["title"] in ("", f"Bài {lecture['number']}")
                         else row["title"])
                content = row["content"] or lecture["content"]
                db.execute("""UPDATE lectures SET title=?, content=?, estimated_hours=?,
                    remaining_hours=? WHERE id=?""", (title, content, hours, hours, lecture_id))
            for position, task in enumerate(lecture["tasks"], 1):
                db.execute("""INSERT INTO lecture_tasks
                    (lecture_id, position, kind, title, content, estimated_hours, remaining_hours)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (lecture_id, position, task["type"], task["title"], task["content"],
                     task["hours"], task["hours"]))
            sync_lecture_hours(db, lecture_id)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    db.execute("""INSERT INTO plan_imports
        (source_name, payload_sha256, course_count, lecture_count, task_count)
        VALUES (?, ?, ?, ?, ?)""",
        (source_name, digest, plan["course_count"], plan["lecture_count"], plan["task_count"]))
    replan(reason=f"Nhập kế hoạch AI: {source_name or 'JSON dán trực tiếp'}")
    return counts
