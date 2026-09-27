import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from .auth import current_user_id
from .db import get_db
from .plan_import import TASK_TYPES
from .scheduler import active_course_ids
from .timezone import local_today


def course_progress(course):
    total = course["total_hours"]
    if total > 0:
        return round(100 * (total - course["remaining_hours_total"]) / total)
    return course["progress"]


def courses_with_progress(user_id=None):
    """Courses owned by `user_id` (default: the signed-in user) with computed progress."""
    user_id = current_user_id() if user_id is None else user_id
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
        WHERE c.owner_user_id=? GROUP BY c.id ORDER BY c.id""", (user_id,)
    ).fetchall()
    courses = []
    for row in rows:
        course = dict(row)
        course["name"] = course.get("display_name") or course["name"]
        course["target_date"] = course.pop("calculated_target_date")
        course["display_progress"] = course_progress(course)
        # The stored status is only set by hand (or to "Hoàn thành" by the
        # scheduler), so a course with finished work would still read
        # "Chưa bắt đầu". Show it as in progress instead.
        if course["status"] == "Chưa bắt đầu" and (
                course["completed_count"]
                or course["remaining_hours_total"] < course["total_hours"]):
            course["status"] = "Đang học"
        courses.append(course)
    return courses


def completed_work(user_id=None):
    """Finished work items with their completion date and estimated hours.

    A lecture split into parts counts through its parts; a lecture without
    parts counts as one item. Hours are the planned estimate of the item.
    """
    user_id = current_user_id() if user_id is None else user_id
    rows = get_db().execute("""
        SELECT t.completed_at AS date, c.id AS course_id, c.display_name AS course_name,
               l.lecture_number, t.title, t.estimated_hours AS hours
        FROM lecture_tasks t JOIN lectures l ON l.id=t.lecture_id
        JOIN courses c ON c.id=l.course_id
        WHERE c.owner_user_id=? AND t.status='Hoàn thành' AND t.completed_at IS NOT NULL
        UNION ALL
        SELECT l.completed_at, c.id, c.display_name, l.lecture_number, l.title, l.estimated_hours
        FROM lectures l JOIN courses c ON c.id=l.course_id
        WHERE c.owner_user_id=? AND l.status='Hoàn thành' AND l.completed_at IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM lecture_tasks t WHERE t.lecture_id=l.id)
        ORDER BY 1, 2, 4""", (user_id, user_id)).fetchall()
    return [dict(row) for row in rows]


def work_summary(work, start, end):
    """Completed work between two ISO dates, inclusive."""
    items = [row for row in work if start <= row["date"] <= end]
    by_course = defaultdict(float)
    for row in items:
        by_course[row["course_name"]] += row["hours"]
    return {"hours": round(sum(by_course.values()), 2),
            "by_course": {name: round(hours, 2) for name, hours in by_course.items()},
            "items": items}


def report_data():
    db = get_db()
    user_id = current_user_id()
    courses = courses_with_progress()
    lectures = [dict(row) for row in db.execute(
        """SELECT l.* FROM lectures l JOIN courses c ON c.id=l.course_id
        WHERE c.owner_user_id=? ORDER BY l.course_id, l.lecture_number""", (user_id,))]
    tasks = [dict(row) for row in db.execute(
        """SELECT t.* FROM lecture_tasks t JOIN lectures l ON l.id=t.lecture_id
        JOIN courses c ON c.id=l.course_id WHERE c.owner_user_id=?
        ORDER BY t.lecture_id, t.position""", (user_id,))]
    journals = [dict(row) for row in db.execute(
        """SELECT j.*, c.display_name AS course_name FROM journals j
        JOIN courses c ON c.id = j.course_id WHERE j.user_id=? ORDER BY j.date, j.id""",
        (user_id,))]
    reviews = [dict(row) for row in db.execute(
        "SELECT * FROM user_reviews WHERE user_id=? ORDER BY period_start", (user_id,))]
    allocations = [dict(row) for row in db.execute(
        """SELECT a.* FROM schedule_allocations a JOIN courses c ON c.id=a.course_id
        WHERE c.owner_user_id=? ORDER BY a.study_date, a.id""", (user_id,))]
    profiles = [dict(row) for row in db.execute(
        "SELECT * FROM user_capacity_profiles WHERE user_id=? ORDER BY effective_from", (user_id,))]
    overrides = [dict(row) for row in db.execute(
        "SELECT * FROM user_capacity_overrides WHERE user_id=? ORDER BY study_date", (user_id,))]
    schedule_events = [dict(row) for row in db.execute(
        """SELECT e.* FROM schedule_events e JOIN courses c ON c.id=e.course_id
        WHERE c.owner_user_id=? ORDER BY e.id""", (user_id,))]
    completion_events = [dict(row) for row in db.execute(
        """SELECT e.* FROM completion_events e JOIN courses c ON c.id=e.course_id
        WHERE c.owner_user_id=? ORDER BY e.id""", (user_id,))]
    missed = [dict(row) for row in db.execute(
        """SELECT m.* FROM missed_deadlines m JOIN lectures l ON l.id=m.lecture_id
        JOIN courses c ON c.id=l.course_id WHERE c.owner_user_id=? ORDER BY m.id""", (user_id,))]
    imports = [dict(row) for row in db.execute(
        "SELECT * FROM plan_imports WHERE user_id=? ORDER BY id", (user_id,))]
    plans = [dict(row) for row in db.execute(
        "SELECT * FROM study_plans WHERE owner_user_id=? ORDER BY priority, id", (user_id,))]
    plan_courses = [dict(row) for row in db.execute(
        """SELECT pc.* FROM plan_courses pc JOIN study_plans p ON p.id=pc.plan_id
        WHERE p.owner_user_id=? ORDER BY pc.plan_id, pc.position""", (user_id,))]
    return {"courses": courses, "lectures": lectures, "lecture_tasks": tasks,
            "journals": journals, "plan_imports": imports,
            "study_plans": plans, "plan_courses": plan_courses,
            "reviews": reviews, "schedule_allocations": allocations,
            "capacity_profiles": profiles, "capacity_overrides": overrides,
            "schedule_events": schedule_events, "completion_events": completion_events,
            "missed_deadlines": missed}


def streak(journals, today=None):
    days = {date.fromisoformat(row["date"]) for row in journals}
    day = today or local_today()
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
    planned_hours = round(sum(c["total_hours"] for c in courses), 2)
    done_hours = round(sum(c["total_hours"] - c["remaining_hours_total"] for c in courses), 2)
    hours_line = (f"Giờ đã hoàn thành: {done_hours:g}/{planned_hours:g}; "
                  f"giờ ghi trong nhật ký: {total_hours:g}")
    active_ids = set(active_course_ids())
    active_courses = [course for course in courses if course["id"] in active_ids]
    current = next((c for c in active_courses if c["status"] == "Đang học"), None)
    if current is None:
        current = next((c for c in active_courses if c["display_progress"] < 100), None)
    weak = [row for row in journals if row["problems"] or
            (row["understanding"] is not None and row["understanding"] <= 5)]
    next_plan = next((row["next_plan"] for row in reversed(journals) if row["next_plan"]), "—")

    summary = ["# Tiến độ kế hoạch", "", f"Ngày xuất: {local_today().isoformat()}", "",
               f"Khóa học hiện tại: {current['name'] if current else '—'}", "",
               hours_line, "", "## Khóa học", ""]
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

    streak_days = streak(journals + completed_work())
    progress = ["# Báo cáo tiến độ", "", hours_line,
                f"Số ngày liên tiếp có tiến độ: {streak_days}", "", "## Tiến độ bài học", ""]
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
