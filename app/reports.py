import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from .db import get_db
from .plan_import import TASK_TYPES
from .scheduler import active_course_ids


def course_progress(course):
    total = course["total_hours"]
    if total > 0:
        return round(100 * (total - course["remaining_hours_total"]) / total)
    return course["progress"]


def courses_with_progress():
    rows = get_db().execute(
        """SELECT c.*, COUNT(l.id) AS lecture_count,
        SUM(CASE WHEN l.status = 'Hoàn thành' THEN 1 ELSE 0 END) AS completed_count,
        CASE WHEN COUNT(l.id)>0 THEN COALESCE(SUM(l.estimated_hours), 0)
             ELSE c.estimated_hours END AS total_hours,
        CASE WHEN c.status='Hoàn thành' THEN 0
             WHEN COUNT(l.id)>0 THEN COALESCE(SUM(l.remaining_hours), 0)
             ELSE c.estimated_hours END AS remaining_hours_total,
        c.target_date AS calculated_target_date
        FROM courses c LEFT JOIN lectures l ON l.course_id = c.id
        GROUP BY c.id ORDER BY c.id"""
    ).fetchall()
    courses = []
    for row in rows:
        course = dict(row)
        course["target_date"] = course.pop("calculated_target_date")
        course["display_progress"] = course_progress(course)
        courses.append(course)
    return courses


def report_data():
    db = get_db()
    courses = courses_with_progress()
    lectures = [dict(row) for row in db.execute(
        "SELECT * FROM lectures ORDER BY course_id, lecture_number"
    )]
    tasks = [dict(row) for row in db.execute(
        "SELECT * FROM lecture_tasks ORDER BY lecture_id, position"
    )]
    journals = [dict(row) for row in db.execute(
        """SELECT j.*, c.name AS course_name FROM journals j
        JOIN courses c ON c.id = j.course_id ORDER BY j.date, j.id"""
    )]
    reviews = [dict(row) for row in db.execute("SELECT * FROM reviews ORDER BY period_start")]
    allocations = [dict(row) for row in db.execute(
        "SELECT * FROM schedule_allocations ORDER BY study_date, id")]
    profiles = [dict(row) for row in db.execute(
        "SELECT * FROM capacity_profiles ORDER BY effective_from")]
    overrides = [dict(row) for row in db.execute(
        "SELECT * FROM capacity_overrides ORDER BY study_date")]
    schedule_events = [dict(row) for row in db.execute(
        "SELECT * FROM schedule_events ORDER BY id")]
    completion_events = [dict(row) for row in db.execute(
        "SELECT * FROM completion_events ORDER BY id")]
    missed = [dict(row) for row in db.execute(
        "SELECT * FROM missed_deadlines ORDER BY id")]
    imports = [dict(row) for row in db.execute("SELECT * FROM plan_imports ORDER BY id")]
    plans = [dict(row) for row in db.execute("SELECT * FROM study_plans ORDER BY priority, id")]
    plan_courses = [dict(row) for row in db.execute(
        "SELECT * FROM plan_courses ORDER BY plan_id, position")]
    return {"courses": courses, "lectures": lectures, "lecture_tasks": tasks,
            "journals": journals, "plan_imports": imports,
            "study_plans": plans, "plan_courses": plan_courses,
            "reviews": reviews, "schedule_allocations": allocations,
            "capacity_profiles": profiles, "capacity_overrides": overrides,
            "schedule_events": schedule_events, "completion_events": completion_events,
            "missed_deadlines": missed}


def streak(journals, today=None):
    days = {date.fromisoformat(row["date"]) for row in journals}
    day = today or date.today()
    if day not in days:
        day -= timedelta(days=1)
    count = 0
    while day in days:
        count += 1
        day -= timedelta(days=1)
    return count


def period_summary(journals, start, end):
    entries = [row for row in journals if start <= row["date"] <= end]
    hours = defaultdict(float)
    topics = []
    for row in entries:
        hours[row["course_name"]] += row["hours"]
        if row["topic"] and row["topic"] not in topics:
            topics.append(row["topic"])
    return {"hours": round(sum(hours.values()), 2), "by_course": dict(hours),
            "topics": topics, "entries": len(entries)}


def build_export(export_dir):
    data = report_data()
    courses, lectures, journals = data["courses"], data["lectures"], data["journals"]
    total_hours = round(sum(row["hours"] for row in journals), 2)
    active_ids = set(active_course_ids())
    active_courses = [course for course in courses if course["id"] in active_ids]
    current = next((c for c in active_courses if c["status"] == "Đang học"), None)
    if current is None:
        current = next((c for c in active_courses if c["display_progress"] < 100), None)
    weak = [row for row in journals if row["problems"] or
            (row["understanding"] is not None and row["understanding"] <= 5)]
    next_plan = next((row["next_plan"] for row in reversed(journals) if row["next_plan"]), "—")

    summary = ["# Tiến độ học Quant", "", f"Ngày xuất: {date.today().isoformat()}", "",
               f"Khóa học hiện tại: {current['name'] if current else '—'}", "",
               f"Tổng số giờ học: {total_hours:g}", "", "## Khóa học", ""]
    for course in courses:
        summary.append(f"- {course['name']}: {course['display_progress']}% · "
                       f"còn {course['remaining_hours_total']:g}/{course['total_hours']:g} giờ "
                       f"({course['status']})")
    summary += ["", "## Kế hoạch học", ""]
    for plan in data["study_plans"]:
        names = [next(c["name"] for c in courses if c["id"] == member["course_id"])
                 for member in data["plan_courses"] if member["plan_id"] == plan["id"]]
        summary.append(f"- {plan['name']} ({plan['status']}): {', '.join(names) or 'chưa có khóa học'}")
    summary += ["", "## Mục tiêu tiếp theo", "", next_plan, "", "## Nội dung cần ôn tập", ""]
    summary += [f"- {row['date']} · {row['course_name']} · {row['topic'] or 'Chung'}: "
                f"{row['problems'] or 'Điểm hiểu bài thấp'}"
                for row in weak[-10:]] or ["- Chưa ghi nhận"]
    projected = {course["id"]: course["target_date"] for course in active_courses
                 if course["target_date"]}
    missing = [c for c in active_courses if c["status"] != "Hoàn thành" and c["id"] not in projected]
    remaining_dates = [projected[c["id"]] for c in active_courses
                       if c["status"] != "Hoàn thành" and c["id"] in projected]
    summary += ["", "## Dự báo hoàn thành", "",
                f"Toàn bộ lộ trình: {max(remaining_dates) if remaining_dates and not missing else 'Chưa đủ dữ liệu'}", ""]
    for course in active_courses:
        summary.append(f"- {course['name']}: " +
                       ("Đã hoàn thành" if course["status"] == "Hoàn thành" else
                        projected.get(course["id"], "Chưa đủ dữ liệu")))

    streak_days = streak(journals)
    progress = ["# Báo cáo tiến độ", "", f"Tổng số giờ học: {total_hours:g}",
                f"Số ngày học liên tiếp: {streak_days}", "", "## Tiến độ bài học", ""]
    for course in courses:
        group = [row for row in lectures if row["course_id"] == course["id"]]
        completed = [str(row["lecture_number"]) for row in group if row["status"] == "Hoàn thành"]
        progress.append(f"- {course['name']}: còn {course['remaining_hours_total']:g}/"
                        f"{course['total_hours']:g} giờ; đã hoàn thành {len(completed)}/{len(group)} bài"
                        + (f" (bài {', '.join(completed)})" if completed else ""))
    progress += ["", "## Phần việc của bài học", ""]
    for task in data["lecture_tasks"]:
        progress.append(f"- Bài ID {task['lecture_id']} · {task['title']} "
                        f"({TASK_TYPES.get(task['kind'], task['kind'])}): "
                        f"còn {task['remaining_hours']:g}/{task['estimated_hours']:g} giờ, "
                        f"hạn {task['deadline'] or 'chưa có'}, {task['status']}")
    if not data["lecture_tasks"]:
        progress.append("- Chưa có phần việc")
    progress += ["", "## Buổi học gần đây", ""]
    for row in reversed(journals[-20:]):
        progress += [f"### {row['date']} · {row['course_name']}", "",
                     f"Chủ đề: {row['topic'] or '—'}; Bài học: {row['lecture'] or '—'}; "
                     f"Số giờ: {row['hours']:g}; Mức độ hiểu: {row['understanding'] or '—'}/10", "",
                     f"Ghi chú: {row['notes'] or '—'}", "",
                     f"Vướng mắc: {row['problems'] or '—'}", "",
                     f"Kế hoạch tiếp theo: {row['next_plan'] or '—'}", ""]
    progress += ["## Nhìn lại", ""]
    for row in reversed(data["reviews"]):
        period_label = "Tuần" if row["period"] == "week" else "Tháng"
        progress += [f"### {period_label} bắt đầu {row['period_start']}", "",
                     f"Điểm mạnh: {row['strength'] or '—'}", "",
                     f"Điểm yếu: {row['weakness'] or '—'}", "",
                     f"Kế hoạch hành động: {row['action_plan'] or '—'}", ""]
    progress += ["## Bài học trễ hạn", ""]
    for row in data["missed_deadlines"]:
        progress.append(f"- Bài ID {row['lecture_id']}"
                        f"{', phần việc ID ' + str(row['task_id']) if row['task_id'] else ''}: "
                        f"{row['old_deadline']} → "
                        f"{row['new_deadline'] or 'Đang tính lại'}; lý do: {row['reason']}")
    if not data["missed_deadlines"]:
        progress.append("- Chưa ghi nhận")
    progress += ["", "## Thay đổi deadline gần đây", ""]
    for row in data["schedule_events"][-30:]:
        progress.append(f"- {row['changed_at']} · khóa ID {row['course_id']}"
                        f"{', bài ID ' + str(row['lecture_id']) if row['lecture_id'] else ''}"
                        f"{', phần việc ID ' + str(row['task_id']) if row['task_id'] else ''}: "
                        f"{row['old_deadline'] or '—'} → {row['new_deadline'] or '—'}; {row['reason']}")
    if not data["schedule_events"]:
        progress.append("- Chưa ghi nhận")

    path = Path(export_dir)
    path.mkdir(parents=True, exist_ok=True)
    files = {
        "learning_summary.md": "\n".join(summary) + "\n",
        "progress_report.md": "\n".join(progress) + "\n",
        "learning_data.json": json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    }
    for name, content in files.items():
        (path / name).write_text(content, encoding="utf-8")
    return files
