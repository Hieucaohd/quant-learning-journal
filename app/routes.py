import sqlite3
import calendar
import json
from contextlib import closing
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template,
                   request, send_file, url_for)

from .ai_kit import ai_kit_files, ai_prompt, course_replan_kit_files
from .auth import current_user_id
from .course_transfer import (apply_course_overwrite, inspect_course_overwrite,
                              parse_course_snapshot, snapshot_json)
from .db import enroll_catalog_course, get_db, sync_lecture_hours
from .reports import (build_export, completed_work, courses_with_progress, period_summary,
                      report_data, streak, work_summary)
from .scheduler import (active_course_ids, allocation_segment_metadata, capacity_on,
                        course_deadlines, day_plan, ensure_plan, pending_missed, replan)
from .timezone import local_today
from .plan_import import TASK_TYPES, apply_plan, inspect_plan, parse_plan


bp = Blueprint("main", __name__)
STATUSES = ("Chưa bắt đầu", "Đang học", "Hoàn thành", "Cần ôn tập")
DIFFICULTIES = ("Dễ", "Trung bình", "Khó")


def positive_hours(value, allow_zero=False):
    try:
        hours = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Số giờ phải là số") from exc
    if not (0 <= hours <= 24 if allow_zero else 0 < hours <= 24):
        raise ValueError("Số giờ phải nằm trong khoảng 0–24")
    return round(hours, 2)


def workload_hours(value):
    try:
        hours = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Thời lượng ước tính phải là số") from exc
    if not 0 <= hours <= 10000:
        raise ValueError("Thời lượng ước tính phải từ 0 đến 10.000 giờ")
    return round(hours, 2)


def parse_date(value, optional=False):
    if optional and not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError("Ngày không hợp lệ") from exc


def optional_score(value):
    if not value:
        return None
    try:
        score = int(value)
    except ValueError as exc:
        raise ValueError("Điểm phải là số từ 1 đến 10") from exc
    if not 1 <= score <= 10:
        raise ValueError("Điểm phải từ 1 đến 10")
    return score


def required_row(table, row_id):
    user_id = current_user_id()
    if table == "courses":
        row = get_db().execute("SELECT * FROM courses WHERE id=? AND owner_user_id=?",
                               (row_id, user_id)).fetchone()
    elif table == "study_plans":
        row = get_db().execute("SELECT * FROM study_plans WHERE id=? AND owner_user_id=?",
                               (row_id, user_id)).fetchone()
    elif table == "lectures":
        row = get_db().execute("""SELECT l.* FROM lectures l JOIN courses c ON c.id=l.course_id
            WHERE l.id=? AND c.owner_user_id=?""", (row_id, user_id)).fetchone()
    elif table == "lecture_tasks":
        row = get_db().execute("""SELECT t.* FROM lecture_tasks t
            JOIN lectures l ON l.id=t.lecture_id JOIN courses c ON c.id=l.course_id
            WHERE t.id=? AND c.owner_user_id=?""", (row_id, user_id)).fetchone()
    elif table == "journals":
        row = get_db().execute("SELECT * FROM journals WHERE id=? AND user_id=?",
                               (row_id, user_id)).fetchone()
    else:
        row = get_db().execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def visible_plans(user_id):
    """Plans the user may see on the schedule, tagged by group.

    `mine`: owned plans; `shared`: shared with the user; `others`: every other
    plan, admins only. Other people's plans are always read-only.
    """
    is_admin = g.user is not None and g.user["role"] == "admin"
    plans = []
    for row in get_db().execute("""SELECT p.*, u.username AS owner_name,
        EXISTS (SELECT 1 FROM plan_shares ps WHERE ps.plan_id=p.id AND ps.user_id=?) AS is_shared
        FROM study_plans p JOIN users u ON u.id=p.owner_user_id
        ORDER BY p.owner_user_id != ?, u.username, p.priority, p.id""", (user_id, user_id)):
        plan = dict(row)
        if plan["owner_user_id"] == user_id:
            plan["group"] = "mine"
        elif plan["is_shared"]:
            plan["group"] = "shared"
        elif is_admin:
            plan["group"] = "others"
        else:
            continue
        plan["label"] = (plan["name"] if plan["group"] == "mine"
                         else f"{plan['name']} · @{plan['owner_name']}")
        plans.append(plan)
    return plans


def backup_database(label):
    db = get_db()
    if getattr(db, "is_remote", False):
        payload = json.dumps(report_data(), ensure_ascii=False)
        backup_id = db.execute("""INSERT INTO deletion_backups (label, payload_json, user_id)
            VALUES (?, ?, ?)""", (label, payload, current_user_id())).lastrowid
        return Path(f"turso-backup-{backup_id}.json")
    backup_dir = Path(current_app.config["BACKUP_DIR"])
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"journal-before-{label}-{stamp}-{uuid4().hex[:8]}.sqlite3"
    with closing(sqlite3.connect(backup_path)) as destination:
        db.backup(destination)
    return backup_path


def refresh_schedule(reason):
    replan(local_today(), reason, preserve_overdue=True)
    if pending_missed():
        flash("Đã cập nhật lịch các việc còn lại. Mục quá hạn vẫn chờ bạn ghi lý do để lùi hạn.", "error")


def return_to_schedule():
    values = {"date": request.form.get("return_date", local_today().isoformat())}
    if request.form.get("filter") == "1":
        values["filter"] = 1
        values["plan_id"] = request.form.getlist("plan_id")
    return redirect(url_for("main.schedule", **values))


def return_after_completion(course_id):
    if request.form.get("return_to_course") == "1":
        return redirect(url_for("main.course_detail", course_id=course_id))
    return return_to_schedule()


def update_item_deadline(kind, item_id):
    db = get_db()
    item = required_row("lectures" if kind == "lecture" else "lecture_tasks", item_id)
    lecture = item if kind == "lecture" else required_row("lectures", item["lecture_id"])
    course = required_row("courses", lecture["course_id"])
    try:
        if item["status"] == "Hoàn thành":
            raise ValueError("Mục này đã hoàn thành; hãy sửa ngày hoàn thành thực tế")
        desired = parse_date(request.form.get("deadline", ""), True)
        if desired and course["start_date"] and desired < course["start_date"]:
            if desired > local_today().isoformat():
                raise ValueError("Chỉ có thể ghi nhận hoàn thành trước ngày bắt đầu nếu ngày đó đã qua")
            if kind == "lecture":
                return complete_lecture(item_id, completed_at_override=desired)
            return complete_task(item_id, completed_at_override=desired)
        if desired and desired < local_today().isoformat():
            raise ValueError("Hạn trong quá khứ cần được ghi ở ô ngày hoàn thành thực tế")
        reason = request.form.get("reason", "").strip()
        if desired == item["manual_deadline"]:
            raise ValueError("Hạn dự kiến chưa thay đổi")
        if not reason:
            raise ValueError("Hãy ghi lý do khi đổi hạn dự kiến")
        table = "lectures" if kind == "lecture" else "lecture_tasks"
        last_event_id = db.execute(
            "SELECT COALESCE(MAX(id), 0) FROM schedule_events").fetchone()[0]
        db.execute(f"UPDATE {table} SET manual_deadline=? WHERE id=?", (desired, item_id))
        replan(local_today(), f"Đổi hạn dự kiến: {reason}", preserve_overdue=True)
        updated = db.execute(f"SELECT deadline FROM {table} WHERE id=?", (item_id,)).fetchone()
        final_deadline = updated["deadline"]
        if kind == "lecture":
            db.execute("""DELETE FROM schedule_events WHERE id>? AND lecture_id=?
                AND task_id IS NULL""", (last_event_id, item_id))
        else:
            db.execute("DELETE FROM schedule_events WHERE id>? AND task_id=?",
                       (last_event_id, item_id))
        db.execute("""INSERT INTO schedule_events
            (course_id, lecture_id, task_id, old_deadline, new_deadline, reason, kind)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (course["id"], lecture["id"], None if kind == "lecture" else item_id,
             item["deadline"], final_deadline, reason,
             "đặt hạn thủ công" if desired else "bỏ hạn thủ công"))
        db.commit()
        flash("Đã lưu hạn dự kiến và ghi thay đổi vào lịch sử.", "success")
    except (ValueError, KeyError) as exc:
        db.rollback()
        flash(str(exc), "error")
    return redirect(url_for("main.course_detail", course_id=course["id"]))


@bp.post("/lectures/<int:lecture_id>/deadline")
def update_lecture_deadline(lecture_id):
    return update_item_deadline("lecture", lecture_id)


@bp.post("/tasks/<int:task_id>/deadline")
def update_task_deadline(task_id):
    return update_item_deadline("task", task_id)


def update_lecture_from_tasks(lecture_id, completed_day=None):
    db = get_db()
    lecture = db.execute("SELECT status, deadline FROM lectures WHERE id=?", (lecture_id,)).fetchone()
    rows = db.execute("""SELECT status, estimated_hours, remaining_hours, completed_at
        FROM lecture_tasks WHERE lecture_id=?""",
                      (lecture_id,)).fetchall()
    sync_lecture_hours(db, lecture_id)
    estimated = round(sum(row["estimated_hours"] for row in rows), 2)
    remaining = round(sum(row["remaining_hours"] for row in rows
                          if row["status"] != "Hoàn thành"), 2)
    all_done = bool(rows) and all(row["status"] == "Hoàn thành" for row in rows)
    any_done = any(row["status"] == "Hoàn thành" for row in rows)
    if all_done:
        done_day = max((row["completed_at"] for row in rows if row["completed_at"]),
                       default=completed_day or local_today().isoformat())
        db.execute("""UPDATE lectures SET estimated_hours=?, remaining_hours=0, status='Hoàn thành',
            completed_at=?, completed_on_time=? WHERE id=?""",
            (estimated, done_day,
             None if lecture["deadline"] is None else int(done_day <= lecture["deadline"]),
             lecture_id))
    else:
        in_progress = any_done or any(
            row["status"] != "Chưa bắt đầu" or row["remaining_hours"] < row["estimated_hours"]
            for row in rows)
        db.execute("""UPDATE lectures SET status=?, completed_at=NULL,
            completed_on_time=NULL WHERE id=?""",
                   ("Đang học" if in_progress else "Chưa bắt đầu", lecture_id))


@bp.get("/")
def dashboard():
    try:
        ensure_plan()
    except ValueError as exc:
        get_db().rollback()
        flash(str(exc), "error")
    data = report_data()
    journals = data["journals"]
    active_ids = set(active_course_ids())
    courses = [course for course in data["courses"] if course["id"] in active_ids]
    current = next((c for c in courses if c["status"] == "Đang học"), None)
    if current is None:
        current = next((c for c in courses if c["display_progress"] < 100), None)
    today = local_today()
    week_start = today - timedelta(days=today.weekday())
    week_days = [week_start + timedelta(days=offset) for offset in range(7)]
    work = completed_work()
    week = work_summary(work, week_days[0].isoformat(), week_days[-1].isoformat())
    planned_hours = round(sum(c["total_hours"] for c in data["courses"]), 2)
    done_hours = round(sum(c["total_hours"] - c["remaining_hours_total"]
                           for c in data["courses"]), 2)
    projections = course_deadlines()
    missing = [c for c in courses if c["status"] != "Hoàn thành" and c["id"] not in projections]
    remaining_dates = [projections[c["id"]] for c in courses
                       if c["status"] != "Hoàn thành" and c["id"] in projections]
    return render_template("dashboard.html", current=current, courses=courses,
                           recent=list(reversed(journals[-7:])),
                           planned_hours=planned_hours, done_hours=done_hours,
                           done_percent=round(100 * done_hours / planned_hours) if planned_hours else 0,
                           journal_hours=round(sum(j["hours"] for j in journals), 2),
                           streak=streak(journals + work), week=week,
                           week_capacity=round(sum(capacity_on(day) for day in week_days), 2),
                           today_plan=day_plan(today),
                           projected_finish=max(remaining_dates) if remaining_dates and not missing else None)


@bp.get("/schedule")
def schedule():
    db = get_db()
    today = local_today()
    try:
        ensure_plan()
    except ValueError as exc:
        db.rollback()
        flash(str(exc), "error")
    try:
        selected = date.fromisoformat(request.args.get("date", today.isoformat()))
    except ValueError:
        abort(400)
    month_start = selected.replace(day=1)
    month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    calendar_weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(selected.year, selected.month)
    calendar_start = calendar_weeks[0][0].isoformat()
    calendar_end = calendar_weeks[-1][-1].isoformat()
    user_id = current_user_id()
    plans = visible_plans(user_id)
    plan_by_id = {plan["id"]: plan for plan in plans}
    if request.args.get("filter") == "1":
        try:
            selected_ids = [int(value) for value in request.args.getlist("plan_id")]
        except ValueError:
            abort(400)
        if any(plan_id not in plan_by_id for plan_id in selected_ids):
            abort(400)
    else:
        selected_ids = [plan["id"] for plan in plans
                        if plan["status"] == "Đang hoạt động" and plan["group"] != "others"]
    selected_ids = list(dict.fromkeys(selected_ids))
    selected_course_ids = set()
    plan_labels = {}
    if selected_ids:
        placeholders = ",".join("?" for _ in selected_ids)
        for row in db.execute(f"""SELECT pc.course_id, p.id FROM plan_courses pc
            JOIN study_plans p ON p.id=pc.plan_id WHERE p.status='Đang hoạt động'
            AND p.id IN ({placeholders}) ORDER BY p.priority, p.id""", selected_ids):
            selected_course_ids.add(row["course_id"])
            plan_labels.setdefault(row["course_id"], []).append(plan_by_id[row["id"]]["label"])
    own_course_ids = {row["id"] for row in db.execute(
        "SELECT id FROM courses WHERE owner_user_id=?", (user_id,))}
    allocation_rows = []
    if selected_course_ids:
        course_placeholders = ",".join("?" for _ in selected_course_ids)
        allocation_rows = db.execute(f"""SELECT a.study_date, a.course_id, a.lecture_id,
            a.task_id, a.hours, c.display_name AS course_name, l.lecture_number,
            l.title AS lecture_title, t.title AS part_title
            FROM schedule_allocations a
            JOIN courses c ON c.id=a.course_id
            LEFT JOIN lectures l ON l.id=a.lecture_id
            LEFT JOIN lecture_tasks t ON t.id=a.task_id
            WHERE a.course_id IN ({course_placeholders})
            ORDER BY a.study_date, a.id""", sorted(selected_course_ids)).fetchall()
    segment_metadata = allocation_segment_metadata(allocation_rows)
    day_summaries = {}
    for row in allocation_rows:
        if not calendar_start <= row["study_date"] <= calendar_end:
            continue
        item = day_summaries.setdefault(row["study_date"], {
            "hours": 0, "shared_hours": 0, "course_ids": set(), "work_by_key": {}})
        item["hours" if row["course_id"] in own_course_ids else "shared_hours"] += row["hours"]
        item["course_ids"].add(row["course_id"])
        if row["task_id"] is not None:
            work_key = ("task", row["task_id"])
            lecture_label = row["lecture_title"] or "Chưa có tiêu đề"
            work_label = (f"Bài {row['lecture_number']}: {lecture_label}"
                          f" · {row['part_title']}")
        elif row["lecture_id"] is not None:
            work_key = ("lecture", row["lecture_id"])
            work_label = f"Bài {row['lecture_number']} · {row['lecture_title'] or 'Chưa có tiêu đề'}"
        else:
            work_key = ("course", row["course_id"])
            work_label = row["course_name"]
        work_item = item["work_by_key"].setdefault(work_key, {
            "key": work_key, "label": work_label, "hours": 0,
            "is_shared": row["course_id"] not in own_course_ids,
        })
        work_item["hours"] = round(work_item["hours"] + row["hours"], 2)
    for study_date, item in day_summaries.items():
        course_ids = item.pop("course_ids")
        item["courses"] = len(course_ids & own_course_ids)
        item["plans"] = list(dict.fromkeys(
            name for course_id in sorted(course_ids)
            for name in plan_labels.get(course_id, [])))
        item["work_items"] = list(item.pop("work_by_key").values())
        for work_item in item["work_items"]:
            work_item.update(segment_metadata[(work_item.pop("key"), study_date)])
    owner_names = {plan["owner_user_id"]: plan["owner_name"] for plan in plans}
    shared_courses = [
        dict(course, owner_name=owner_names[owner_id])
        for owner_id in dict.fromkeys(plan_by_id[plan_id]["owner_user_id"]
                                      for plan_id in selected_ids)
        if owner_id != user_id
        for course in courses_with_progress(owner_id)
        if course["id"] in selected_course_ids]
    courses = [course for course in courses_with_progress()
               if course["id"] in selected_course_ids]
    projections = course_deadlines()
    missing = [course for course in courses if course["status"] != "Hoàn thành"
               and course["id"] not in projections]
    remaining_dates = [projections[c["id"]] for c in courses
                       if c["status"] != "Hoàn thành" and c["id"] in projections]
    all_done = max(remaining_dates) if remaining_dates and not missing else None
    profiles = [dict(row) for row in db.execute(
        "SELECT * FROM user_capacity_profiles WHERE user_id=? ORDER BY effective_from DESC",
        (user_id,))]
    active_profile = next(row for row in profiles if row["effective_from"] <= today.isoformat())
    overrides = [dict(row) for row in db.execute(
        """SELECT * FROM user_capacity_overrides WHERE user_id=? AND study_date>=?
        ORDER BY study_date LIMIT 20""", (user_id, today.isoformat()))]
    events = [dict(row) for row in db.execute("""SELECT e.*, c.display_name AS course_name,
        l.lecture_number, t.title AS part_title FROM schedule_events e
        JOIN courses c ON c.id=e.course_id
        LEFT JOIN lectures l ON l.id=e.lecture_id
        LEFT JOIN lecture_tasks t ON t.id=e.task_id
        WHERE c.owner_user_id=? ORDER BY e.id DESC LIMIT 300""", (user_id,))
              if row["course_id"] in selected_course_ids][:30]
    capacity_events = [dict(row) for row in db.execute(
        "SELECT * FROM user_capacity_events WHERE user_id=? ORDER BY id DESC LIMIT 20",
        (user_id,))]
    pending = [item for item in pending_missed()
               if item["course_id"] in selected_course_ids]
    today_tasks = day_plan(today, selected_ids)
    day_tasks = day_plan(selected, selected_ids, include_others=True)
    return render_template("schedule.html", selected=selected, today=today,
                           month_start=month_start, calendar_weeks=calendar_weeks,
                           prev_month=month_start-timedelta(days=1),
                           next_month=month_end+timedelta(days=1),
                           day_summaries=day_summaries,
                           day_tasks=day_tasks,
                           own_day_hours=sum(item["hours"] for item in day_tasks
                                             if not item["is_shared"]),
                           shared_courses=shared_courses,
                           capacity=capacity_on(selected), courses=courses,
                           projections=projections, missing=missing, all_done=all_done,
                           pending=pending, today_tasks=today_tasks,
                           profiles=profiles, overrides=overrides,
                           events=events, capacity_events=capacity_events,
                           active_hours=json.loads(active_profile["hours_json"]),
                           task_types=TASK_TYPES, plans=plans,
                           selected_ids=selected_ids, plan_labels=plan_labels)


@bp.post("/schedule/capacity")
def capacity_profile():
    db = get_db()
    user_id = current_user_id()
    try:
        effective = parse_date(request.form["effective_from"])
        if effective < local_today().isoformat():
            raise ValueError("Ngày áp dụng phải là hôm nay hoặc một ngày sau đó")
        reason = request.form.get("reason", "").strip()
        if not reason:
            raise ValueError("Vui lòng ghi lý do thay đổi số giờ học")
        hours = [positive_hours(request.form.get(f"day_{i}"), True) for i in range(7)]
        old = db.execute("""SELECT * FROM user_capacity_profiles
            WHERE user_id=? AND effective_from=?""", (user_id, effective)).fetchone()
        old_hours = old["hours_json"] if old else None
        encoded = json.dumps(hours)
        db.execute("""INSERT INTO user_capacity_profiles (user_id, effective_from, hours_json, reason)
            VALUES (?, ?, ?, ?) ON CONFLICT(user_id, effective_from) DO UPDATE SET
            hours_json=excluded.hours_json, reason=excluded.reason,
            created_at=CURRENT_TIMESTAMP""", (user_id, effective, encoded, reason))
        db.execute("""INSERT INTO user_capacity_events
            (user_id, effective_date, old_hours, new_hours, reason, kind)
            VALUES (?, ?, ?, ?, ?, 'lịch tuần')""",
            (user_id, effective, old_hours, encoded, reason))
        refresh_schedule(f"Thay đổi số giờ học từ {effective}: {reason}")
        db.commit()
        flash("Đã cập nhật giờ học và tính lại các hạn hoàn thành.", "success")
    except (ValueError, KeyError) as exc:
        db.rollback()
        flash(str(exc), "error")
    return redirect(url_for("main.schedule"))


@bp.post("/schedule/override")
def capacity_override():
    db = get_db()
    user_id = current_user_id()
    try:
        study_date = parse_date(request.form["study_date"])
        if study_date < local_today().isoformat():
            raise ValueError("Chỉ có thể đổi số giờ của hôm nay hoặc ngày sau")
        hours = positive_hours(request.form.get("hours"), True)
        reason = request.form.get("reason", "").strip()
        if not reason:
            raise ValueError("Vui lòng ghi lý do thay đổi số giờ học")
        old = db.execute("""SELECT hours FROM user_capacity_overrides
            WHERE user_id=? AND study_date=?""", (user_id, study_date)).fetchone()
        db.execute("""INSERT INTO user_capacity_overrides (user_id, study_date, hours, reason)
            VALUES (?, ?, ?, ?) ON CONFLICT(user_id, study_date) DO UPDATE SET
            hours=excluded.hours, reason=excluded.reason, created_at=CURRENT_TIMESTAMP""",
            (user_id, study_date, hours, reason))
        db.execute("""INSERT INTO user_capacity_events
            (user_id, effective_date, old_hours, new_hours, reason, kind)
            VALUES (?, ?, ?, ?, ?, 'ngày riêng')""",
            (user_id, study_date, str(old["hours"]) if old else None, str(hours), reason))
        refresh_schedule(f"Đổi số giờ ngày {study_date}: {reason}")
        db.commit()
        flash("Đã cập nhật ngày học và tính lại các hạn hoàn thành.", "success")
    except (ValueError, KeyError) as exc:
        db.rollback()
        flash(str(exc), "error")
    return redirect(url_for("main.schedule", date=request.form.get("study_date", "")))


@bp.post("/lectures/<int:lecture_id>/complete")
def complete_lecture(lecture_id, completed_at_override=None):
    db = get_db()
    lecture = required_row("lectures", lecture_id)
    try:
        completed = parse_date(completed_at_override or
                               request.form.get("completed_at", local_today().isoformat()))
        if completed > local_today().isoformat():
            raise ValueError("Không thể đánh dấu hoàn thành cho ngày trong tương lai")
        already_done = lecture["status"] == "Hoàn thành"
        if already_done and completed == lecture["completed_at"]:
            raise ValueError("Ngày hoàn thành chưa thay đổi")
        latest_part = db.execute("""SELECT MAX(completed_at) FROM lecture_tasks
            WHERE lecture_id=? AND status='Hoàn thành'""", (lecture_id,)).fetchone()[0]
        if latest_part and completed < latest_part:
            raise ValueError("Ngày hoàn thành bài không thể trước phần việc đã hoàn thành")
        late = lecture["deadline"] is not None and completed > lecture["deadline"]
        reason = request.form.get("reason", "").strip()
        if late and not reason and not db.execute(
            "SELECT 1 FROM missed_deadlines WHERE lecture_id=?", (lecture_id,)
        ).fetchone():
            raise ValueError("Bài học hoàn thành sau hạn dự kiến. Hãy ghi lý do trễ hạn.")
        db.execute("""UPDATE lectures SET status='Hoàn thành', completed_at=?,
            completed_on_time=?, remaining_hours=0 WHERE id=?""",
            (completed, None if lecture["deadline"] is None else int(not late), lecture_id))
        db.execute("""INSERT INTO completion_events
            (course_id, lecture_id, old_date, new_date, reason)
            VALUES (?, ?, ?, ?, ?)""",
            (lecture["course_id"], lecture_id, lecture["completed_at"], completed,
             reason or ("Chỉnh ngày hoàn thành" if already_done else "Ghi nhận hoàn thành")))
        db.execute("""UPDATE lecture_tasks SET status='Hoàn thành', remaining_hours=0,
            completed_at=?, completed_on_time=CASE WHEN deadline IS NULL THEN NULL
            ELSE (deadline >= ?) END WHERE lecture_id=? AND status!='Hoàn thành'""",
            (completed, completed, lecture_id))
        if late and reason:
            db.execute("""INSERT INTO missed_deadlines
                (lecture_id, old_deadline, new_deadline, reason) VALUES (?, ?, ?, ?)""",
                (lecture_id, lecture["deadline"], completed, reason))
        db.execute("""INSERT INTO schedule_events
            (course_id, lecture_id, old_deadline, new_deadline, reason, kind)
            VALUES (?, ?, ?, ?, ?, 'hoàn thành')""",
            (lecture["course_id"], lecture_id, lecture["deadline"], completed,
             reason or ("Chỉnh ngày hoàn thành bài học" if already_done else "Hoàn thành bài học")))
        db.execute("DELETE FROM schedule_allocations WHERE lecture_id=? AND study_date>?",
                   (lecture_id, completed))
        replanned = False
        db.execute("SAVEPOINT completion_replan")
        try:
            replan(local_today(), f"Cập nhật ngày hoàn thành bài {lecture['lecture_number']}",
                   preserve_overdue=True)
            replanned = True
        except ValueError:
            db.execute("ROLLBACK TO completion_replan")
        finally:
            db.execute("RELEASE completion_replan")
        db.commit()
        flash(("Đã cập nhật ngày hoàn thành bài học." if already_done else
               "Đã ghi nhận hoàn thành bài học.") +
              (" Đã tính lại hạn của các việc còn lại." if replanned else
               " Hãy xử lý các mục quá hạn hoặc tăng giờ học để tính lại hạn."), "success")
    except (ValueError, KeyError) as exc:
        db.rollback()
        flash(str(exc), "error")
    return return_after_completion(lecture["course_id"])


@bp.post("/lectures/<int:lecture_id>/miss")
def miss_lecture(lecture_id):
    db = get_db()
    lecture = required_row("lectures", lecture_id)
    try:
        if db.execute("SELECT 1 FROM lecture_tasks WHERE lecture_id=?", (lecture_id,)).fetchone():
            raise ValueError("Hãy ghi lý do trễ hạn ở từng phần việc của bài học")
        if lecture["status"] == "Hoàn thành" or not lecture["deadline"]:
            raise ValueError("Bài học không có hạn dự kiến đang chờ xử lý")
        if lecture["deadline"] > local_today().isoformat():
            raise ValueError("Chỉ ghi trễ hạn khi đến ngày hoàn thành dự kiến")
        reason = request.form.get("reason", "").strip()
        if not reason:
            raise ValueError("Vui lòng ghi lý do chưa hoàn thành")
        old = lecture["deadline"]
        db.execute("""INSERT INTO missed_deadlines (lecture_id, old_deadline, reason)
            VALUES (?, ?, ?)""", (lecture_id, old, reason))
        db.execute("UPDATE lectures SET deadline=NULL, manual_deadline=NULL WHERE id=?", (lecture_id,))
        replan(local_today(), f"Bài {lecture['lecture_number']} trễ hạn: {reason}",
               preserve_overdue=True)
        db.commit()
        flash("Đã ghi lý do và lùi hạn hoàn thành.", "success")
    except (ValueError, KeyError) as exc:
        db.rollback()
        flash(str(exc), "error")
    return return_to_schedule()


@bp.route("/import-plan", methods=["GET", "POST"])
def import_plan():
    preview = None
    raw = ""
    source_name = ""
    import_type = request.form.get("import_type", "merge")
    selected_plan_id = request.form.get("plan_id", "")
    if request.method == "POST":
        try:
            upload = request.files.get("plan_file")
            if upload and upload.filename:
                if not upload.filename.lower().endswith(".json"):
                    raise ValueError("Hãy chọn file .json")
                try:
                    raw = upload.read().decode("utf-8-sig")
                except UnicodeDecodeError as exc:
                    raise ValueError("File phải dùng mã UTF-8") from exc
                source_name = upload.filename
            else:
                raw = request.form.get("payload", "").strip()
                source_name = request.form.get("source_name", "").strip()
            if not raw:
                raise ValueError("Hãy chọn file JSON hoặc dán nội dung JSON")
            if import_type == "overwrite_course":
                try:
                    selected_plan_id = int(selected_plan_id)
                except (TypeError, ValueError) as exc:
                    raise ValueError("Hãy chọn kế hoạch đang hoạt động cần ghi đè") from exc
                plan = parse_course_snapshot(raw)
                counts = inspect_course_overwrite(plan, selected_plan_id)
                preview = dict(plan, **counts)
            elif import_type == "merge":
                plan = parse_plan(raw)
                counts = inspect_plan(plan)
                preview = dict(plan, **counts)
            else:
                raise ValueError("Chế độ import không hợp lệ")
            if request.form.get("mode") == "confirm":
                if import_type == "overwrite_course":
                    target_plan_ids = request.form.getlist("target_plan_id")
                    target_plan_ids = [int(value) for value in target_plan_ids]
                    selectable_ids = {item["id"] for item in counts["affected_plans"]
                                      if item["status"] == "Đang hoạt động"}
                    if not target_plan_ids or not set(target_plan_ids) <= selectable_ids:
                        raise ValueError("Hãy chọn ít nhất một kế hoạch đang hoạt động để áp dụng")
                    backup_path = backup_database(f"course-overwrite-{plan['course_id']}")
                    result = apply_course_overwrite(
                        plan, selected_plan_id, target_plan_ids, raw, source_name)
                else:
                    if pending_missed():
                        raise ValueError("Hãy ghi lý do cho các phần việc quá hạn trước khi import")
                    apply_plan(plan, raw, source_name)
                get_db().commit()
                if import_type == "overwrite_course":
                    flash(f"Đã ghi đè {plan['lecture_count']} bài học và {plan['task_count']} phần việc; "
                          f"đã áp dụng vào {result['selected_plan_count']} kế hoạch và lập lại lịch. "
                          f"Bản sao lưu: {backup_path.name}.", "success")
                else:
                    flash(f"Đã nhập {plan['task_count']} phần việc từ {plan['lecture_count']} bài học.",
                          "success")
                    if counts["new_courses"]:
                        flash("Khóa học mới nằm trong danh mục. Hãy thêm vào một kế hoạch đang hoạt động để đưa lên lịch.",
                              "success")
                return redirect(url_for("main.schedule"))
        except (ValueError, KeyError, sqlite3.IntegrityError) as exc:
            get_db().rollback()
            flash(str(exc), "error")
            preview = None
    active_plans = get_db().execute("""SELECT id, name FROM study_plans
        WHERE owner_user_id=? AND status='Đang hoạt động' ORDER BY priority, id""",
                                    (current_user_id(),)).fetchall()
    return render_template("import_plan.html", preview=preview, raw=raw,
                           source_name=source_name, task_types=TASK_TYPES,
                           ai_prompt=ai_prompt(current_user_id()), import_type=import_type,
                           selected_plan_id=str(selected_plan_id), active_plans=active_plans)


@bp.get("/import-plan/ai-kit.zip")
def download_ai_kit():
    """ZIP of the prompt, schema, sample and the user's courses, for ChatGPT."""
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, content in ai_kit_files(current_user_id()).items():
            archive.writestr(f"ai-kit-khoa-hoc/{name}", content)
    output.seek(0)
    return send_file(output, mimetype="application/zip", as_attachment=True,
                     download_name=f"ai-kit-khoa-hoc-{local_today().isoformat()}.zip")


@bp.get("/import-plan/course-replan-kit.zip")
def download_course_replan_kit():
    """Prompt, guide and schema for revising an exported course snapshot."""
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, content in course_replan_kit_files().items():
            archive.writestr(f"bo-prompt-dieu-chinh-khoa-hoc/{name}", content)
    output.seek(0)
    return send_file(output, mimetype="application/zip", as_attachment=True,
                     download_name=f"bo-prompt-dieu-chinh-khoa-hoc-{local_today().isoformat()}.zip")


@bp.get("/plans/<int:plan_id>/courses/<int:course_id>/export.json")
def export_plan_course(plan_id, course_id):
    """Download one course and its complete plan state for AI revision."""
    try:
        payload = snapshot_json(plan_id, course_id)
    except LookupError:
        abort(404)
    return send_file(BytesIO(payload.encode("utf-8")),
                     mimetype="application/json; charset=utf-8", as_attachment=True,
                     download_name=(f"khoa-hoc-{course_id}-ke-hoach-{plan_id}-"
                                    f"{local_today().isoformat()}.json"))


@bp.route("/plans", methods=["GET", "POST"])
def plans():
    db = get_db()
    user_id = current_user_id()
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            if not name:
                raise ValueError("Vui lòng nhập tên kế hoạch")
            priority = int(request.form.get("priority", "100"))
            if not 1 <= priority <= 1000:
                raise ValueError("Độ ưu tiên phải từ 1 đến 1000")
            db.execute("""INSERT INTO study_plans (name, description, priority, owner_user_id)
                VALUES (?, ?, ?, ?)""", (name, request.form.get("description", "").strip(),
                                           priority, user_id))
            db.commit()
            flash("Đã tạo kế hoạch học.", "success")
            return redirect(url_for("main.plans"))
        except (ValueError, sqlite3.IntegrityError) as exc:
            db.rollback()
            flash("Tên kế hoạch đã tồn tại hoặc thông tin không hợp lệ." if isinstance(
                exc, sqlite3.IntegrityError) else str(exc), "error")
    rows = [dict(row) for row in db.execute("""SELECT p.*, COUNT(pc.course_id) AS course_count
        FROM study_plans p LEFT JOIN plan_courses pc ON pc.plan_id=p.id
        WHERE p.owner_user_id=? GROUP BY p.id ORDER BY p.priority, p.id""", (user_id,))]
    shared = db.execute("""SELECT p.*, u.username AS owner_name,
        (SELECT COUNT(*) FROM plan_courses pc WHERE pc.plan_id=p.id) AS course_count
        FROM plan_shares ps JOIN study_plans p ON p.id=ps.plan_id
        JOIN users u ON u.id=p.owner_user_id WHERE ps.user_id=?
        ORDER BY u.username, p.priority, p.id""", (user_id,)).fetchall()
    return render_template("plans.html", plans=rows, shared_plans=shared)


@bp.route("/plans/<int:plan_id>", methods=["GET", "POST"])
def plan_detail(plan_id):
    db = get_db()
    plan = required_row("study_plans", plan_id)
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            status = request.form.get("status", "")
            priority = int(request.form.get("priority", "100"))
            if not name or status not in ("Đang hoạt động", "Tạm dừng") or not 1 <= priority <= 1000:
                raise ValueError("Kiểm tra tên, trạng thái và độ ưu tiên (1–1000)")
            db.execute("""UPDATE study_plans SET name=?, description=?, status=?, priority=?
                WHERE id=?""", (name, request.form.get("description", "").strip(),
                                 status, priority, plan_id))
            refresh_schedule(f"Cập nhật kế hoạch {name}")
            db.commit()
            flash("Đã cập nhật kế hoạch và lịch học.", "success")
            return redirect(url_for("main.plan_detail", plan_id=plan_id))
        except (ValueError, sqlite3.IntegrityError) as exc:
            db.rollback()
            flash("Tên kế hoạch đã tồn tại." if isinstance(exc, sqlite3.IntegrityError)
                  else str(exc), "error")
    members = [dict(row) for row in db.execute("""SELECT c.*, pc.position FROM plan_courses pc
        JOIN courses c ON c.id=pc.course_id WHERE pc.plan_id=? ORDER BY pc.position, c.id""",
        (plan_id,))]
    available = [dict(row) for row in db.execute("""SELECT c.* FROM courses c
        WHERE c.owner_user_id=? AND NOT EXISTS
        (SELECT 1 FROM plan_courses pc WHERE pc.plan_id=? AND pc.course_id=c.id)
        ORDER BY c.id""", (current_user_id(), plan_id))]
    shared_with = db.execute("""SELECT u.id, u.username, u.display_name FROM plan_shares ps
        JOIN users u ON u.id=ps.user_id WHERE ps.plan_id=? ORDER BY u.username""",
        (plan_id,)).fetchall()
    return render_template("plan_detail.html", plan=plan, members=members,
                           available=available, projections=course_deadlines(),
                           shared_with=shared_with)


@bp.post("/plans/<int:plan_id>/share")
def share_plan(plan_id):
    plan = required_row("study_plans", plan_id)
    username = request.form.get("username", "").strip()
    target = get_db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if target is None:
        flash("Không tìm thấy người dùng này.", "error")
    elif target["id"] == current_user_id():
        flash("Bạn đã là chủ sở hữu kế hoạch.", "error")
    else:
        get_db().execute("INSERT OR IGNORE INTO plan_shares (plan_id, user_id) VALUES (?, ?)",
                         (plan_id, target["id"]))
        get_db().commit()
        flash(f"Đã chia sẻ kế hoạch với {target['username']} ở chế độ chỉ xem.", "success")
    return redirect(url_for("main.plan_detail", plan_id=plan["id"]))


@bp.post("/plans/<int:plan_id>/shares/<int:user_id>/remove")
def remove_plan_share(plan_id, user_id):
    required_row("study_plans", plan_id)
    get_db().execute("DELETE FROM plan_shares WHERE plan_id=? AND user_id=?",
                     (plan_id, user_id))
    get_db().commit()
    flash("Đã thu hồi quyền xem kế hoạch.", "success")
    return redirect(url_for("main.plan_detail", plan_id=plan_id))


@bp.get("/shared/plans/<int:plan_id>")
def shared_plan(plan_id):
    db = get_db()
    plan = db.execute("""SELECT p.*, u.username AS owner_name FROM study_plans p
        JOIN users u ON u.id=p.owner_user_id WHERE p.id=?""", (plan_id,)).fetchone()
    if plan is None:
        abort(404)
    allowed = (plan["owner_user_id"] == current_user_id() or g.user["role"] == "admin" or
               db.execute("SELECT 1 FROM plan_shares WHERE plan_id=? AND user_id=?",
                          (plan_id, current_user_id())).fetchone())
    if not allowed:
        abort(403)
    progress = {course["id"]: course
                for course in courses_with_progress(plan["owner_user_id"])}
    members = [progress[row["course_id"]] for row in db.execute(
        "SELECT course_id FROM plan_courses WHERE plan_id=? ORDER BY position, course_id",
        (plan_id,)) if row["course_id"] in progress]
    return render_template("shared_plan.html", plan=plan, members=members)


@bp.post("/plans/<int:plan_id>/courses")
def add_course_to_plan(plan_id):
    db = get_db()
    required_row("study_plans", plan_id)
    try:
        course_id = int(request.form["course_id"])
        required_row("courses", course_id)
        position = db.execute("""SELECT COALESCE(MAX(position), 0)+1 FROM plan_courses
            WHERE plan_id=?""", (plan_id,)).fetchone()[0]
        db.execute("""INSERT INTO plan_courses (plan_id, course_id, position)
            VALUES (?, ?, ?)""", (plan_id, course_id, position))
        refresh_schedule("Thêm khóa học vào kế hoạch")
        db.commit()
        flash("Đã thêm khóa học vào kế hoạch.", "success")
    except (ValueError, KeyError, sqlite3.IntegrityError) as exc:
        db.rollback()
        flash(f"Không thể thêm khóa học: {exc}", "error")
    return redirect(url_for("main.plan_detail", plan_id=plan_id))


@bp.post("/plans/<int:plan_id>/courses/<int:course_id>/remove")
def remove_course_from_plan(plan_id, course_id):
    db = get_db()
    required_row("study_plans", plan_id)
    db.execute("DELETE FROM plan_courses WHERE plan_id=? AND course_id=?",
               (plan_id, course_id))
    try:
        refresh_schedule("Gỡ khóa học khỏi kế hoạch")
        db.commit()
        flash("Đã gỡ khóa học khỏi kế hoạch. Khóa học vẫn nằm trong danh mục.", "success")
    except ValueError as exc:
        db.rollback()
        flash(str(exc), "error")
    return redirect(url_for("main.plan_detail", plan_id=plan_id))


@bp.post("/plans/<int:plan_id>/delete")
def delete_plan(plan_id):
    db = get_db()
    plan = required_row("study_plans", plan_id)
    if request.form.get("name", "").strip() != plan["name"]:
        flash("Tên xác nhận chưa khớp. Kế hoạch chưa bị xóa.", "error")
        return redirect(url_for("main.plan_detail", plan_id=plan_id))
    try:
        db.execute("DELETE FROM plan_shares WHERE plan_id=?", (plan_id,))
        db.execute("DELETE FROM plan_courses WHERE plan_id=?", (plan_id,))
        db.execute("DELETE FROM study_plans WHERE id=?", (plan_id,))
        refresh_schedule(f"Xóa kế hoạch {plan['name']}")
        db.commit()
        flash("Đã xóa kế hoạch. Các khóa học vẫn còn trong danh mục.", "success")
        return redirect(url_for("main.plans"))
    except ValueError as exc:
        db.rollback()
        flash(str(exc), "error")
        return redirect(url_for("main.plan_detail", plan_id=plan_id))


@bp.post("/tasks/<int:task_id>")
def update_task(task_id):
    db = get_db()
    task = required_row("lecture_tasks", task_id)
    lecture = required_row("lectures", task["lecture_id"])
    try:
        if task["status"] == "Hoàn thành":
            raise ValueError("Phần việc đã hoàn thành không thể sửa giờ còn lại")
        estimated = workload_hours(request.form.get("estimated_hours", task["estimated_hours"]))
        remaining = workload_hours(request.form.get("remaining_hours", ""))
        if estimated <= 0 or remaining <= 0:
            raise ValueError("Dùng nút hoàn thành nếu phần việc đã xong")
        if remaining > estimated:
            raise ValueError("Số giờ còn lại không thể lớn hơn tổng giờ phần việc")
        reason = request.form.get("change_reason", "").strip()
        if (remaining != task["remaining_hours"] or estimated != task["estimated_hours"]) and not reason:
            raise ValueError("Hãy ghi lý do khi đổi tổng giờ hoặc số giờ còn lại")
        kind = request.form.get("kind", task["kind"])
        if kind not in TASK_TYPES:
            raise ValueError("Loại phần việc không hợp lệ")
        title = request.form.get("title", "").strip()
        if not title:
            raise ValueError("Phần việc cần có tiêu đề")
        db.execute("""UPDATE lecture_tasks SET kind=?, title=?, content=?, estimated_hours=?,
            remaining_hours=?
            WHERE id=?""", (kind, title, request.form.get("content", "").strip(),
                             estimated, remaining, task_id))
        update_lecture_from_tasks(lecture["id"])
        refresh_schedule(reason or f"Cập nhật phần việc {title}")
        db.commit()
        flash("Đã cập nhật phần việc.", "success")
    except (ValueError, KeyError) as exc:
        db.rollback()
        flash(str(exc), "error")
    return redirect(url_for("main.course_detail", course_id=lecture["course_id"]))


@bp.post("/lectures/<int:lecture_id>/tasks")
def add_task(lecture_id):
    db = get_db()
    lecture = required_row("lectures", lecture_id)
    try:
        if lecture["status"] == "Hoàn thành":
            raise ValueError("Không thể thêm phần việc vào bài học đã hoàn thành")
        kind = request.form.get("kind", "other")
        if kind not in TASK_TYPES:
            raise ValueError("Loại phần việc không hợp lệ")
        title = request.form.get("title", "").strip()
        if not title:
            raise ValueError("Phần việc cần có tiêu đề")
        estimated = workload_hours(request.form.get("estimated_hours", ""))
        if estimated <= 0:
            raise ValueError("Thời lượng phần việc phải lớn hơn 0")
        position = db.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM lecture_tasks WHERE lecture_id=?",
            (lecture_id,)).fetchone()[0]
        task_id = db.execute("""INSERT INTO lecture_tasks
            (lecture_id, position, kind, title, content, estimated_hours,
             remaining_hours, deadline)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (lecture_id, position, kind, title, request.form.get("content", "").strip(),
             estimated, estimated, None)).lastrowid
        update_lecture_from_tasks(lecture_id)
        last_event_id = db.execute(
            "SELECT COALESCE(MAX(id), 0) FROM schedule_events").fetchone()[0]
        replan(local_today(), f"Thêm phần việc {title}", preserve_overdue=True)
        planned_deadline = db.execute(
            "SELECT deadline FROM lecture_tasks WHERE id=?", (task_id,)).fetchone()[0]
        db.execute("DELETE FROM schedule_events WHERE id>? AND task_id=?",
                   (last_event_id, task_id))
        db.execute("""INSERT INTO schedule_events
            (course_id, lecture_id, task_id, old_deadline, new_deadline, reason, kind)
            VALUES (?, ?, ?, NULL, ?, ?, 'thêm phần việc')""",
            (lecture["course_id"], lecture_id, task_id, planned_deadline,
             f"Thêm phần việc {title} vào Bài {lecture['lecture_number']}"))
        db.commit()
        flash("Đã thêm phần việc và tính lại tổng giờ, hạn hoàn thành cùng lịch học.",
              "success")
    except (ValueError, KeyError, sqlite3.IntegrityError) as exc:
        db.rollback()
        flash(f"Không thể thêm phần việc: {exc}", "error")
    return redirect(url_for("main.course_detail", course_id=lecture["course_id"]))


@bp.post("/tasks/<int:task_id>/delete")
def delete_task(task_id):
    db = get_db()
    task = required_row("lecture_tasks", task_id)
    lecture = required_row("lectures", task["lecture_id"])
    course_id = lecture["course_id"]
    if request.form.get("confirmation") != "delete":
        flash("Phần việc chưa bị xóa vì thiếu xác nhận.", "error")
        return redirect(url_for("main.course_detail", course_id=course_id))
    label = f"{task['title']} (Bài {lecture['lecture_number']})"
    try:
        backup_path = backup_database(f"task-{task_id}")
        # Rows that point at the part go first, explicitly: a remote libSQL
        # connection may not enforce ON DELETE CASCADE.
        for table in ("schedule_allocations", "missed_deadlines", "completion_events",
                      "schedule_events"):
            db.execute(f"DELETE FROM {table} WHERE task_id=?", (task_id,))
        db.execute("DELETE FROM lecture_tasks WHERE id=?", (task_id,))
        if db.execute("SELECT 1 FROM lecture_tasks WHERE lecture_id=? LIMIT 1",
                      (lecture["id"],)).fetchone():
            update_lecture_from_tasks(lecture["id"])
        else:
            # Last part gone: keep the lecture's own status and completion date
            # rather than resetting a finished lecture; only its hours change.
            sync_lecture_hours(db, lecture["id"])
        db.execute("""INSERT INTO schedule_events
            (course_id, lecture_id, old_deadline, new_deadline, reason, kind)
            VALUES (?, ?, ?, NULL, ?, 'xóa phần việc')""",
            (course_id, lecture["id"], task["deadline"], f"Xóa phần việc {label}"))
        replan(local_today(), f"Xóa phần việc {label}", preserve_overdue=True)
        db.commit()
    except (OSError, sqlite3.Error, ValueError) as exc:
        db.rollback()
        flash(f"Không thể xóa phần việc: {exc}", "error")
    else:
        flash(f"Đã xóa phần việc {label}, cập nhật giờ của bài và tính lại lịch. "
              f"Bản sao lưu: {backup_path.name}", "success")
    return redirect(url_for("main.course_detail", course_id=course_id))


@bp.post("/tasks/<int:task_id>/complete")
def complete_task(task_id, completed_at_override=None):
    db = get_db()
    task = required_row("lecture_tasks", task_id)
    lecture = required_row("lectures", task["lecture_id"])
    try:
        already_done = task["status"] == "Hoàn thành"
        completed = parse_date(completed_at_override or
                               request.form.get("completed_at", local_today().isoformat()))
        if completed > local_today().isoformat():
            raise ValueError("Không thể đánh dấu hoàn thành cho ngày trong tương lai")
        if already_done and completed == task["completed_at"]:
            raise ValueError("Ngày hoàn thành chưa thay đổi")
        late = task["deadline"] is not None and completed > task["deadline"]
        reason = request.form.get("reason", "").strip()
        if late and not reason and not db.execute(
            "SELECT 1 FROM missed_deadlines WHERE task_id=?", (task_id,)
        ).fetchone():
            raise ValueError("Phần việc hoàn thành trễ. Hãy ghi lý do.")
        db.execute("""UPDATE lecture_tasks SET status='Hoàn thành', remaining_hours=0,
            completed_at=?, completed_on_time=? WHERE id=?""",
            (completed, None if task["deadline"] is None else int(not late), task_id))
        db.execute("""INSERT INTO completion_events
            (course_id, lecture_id, task_id, old_date, new_date, reason)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (lecture["course_id"], lecture["id"], task_id, task["completed_at"],
             completed, reason or ("Chỉnh ngày hoàn thành" if already_done
                                  else "Ghi nhận hoàn thành")))
        if late and reason:
            db.execute("""INSERT INTO missed_deadlines
                (lecture_id, task_id, old_deadline, new_deadline, reason)
                VALUES (?, ?, ?, ?, ?)""",
                (lecture["id"], task_id, task["deadline"], completed, reason))
        db.execute("""INSERT INTO schedule_events
            (course_id, lecture_id, task_id, old_deadline, new_deadline, reason, kind)
            VALUES (?, ?, ?, ?, ?, ?, 'hoàn thành')""",
            (lecture["course_id"], lecture["id"], task_id, task["deadline"], completed,
             reason or ("Chỉnh ngày hoàn thành phần việc" if already_done
                        else "Hoàn thành phần việc")))
        db.execute("DELETE FROM schedule_allocations WHERE task_id=? AND study_date>?",
                   (task_id, completed))
        update_lecture_from_tasks(lecture["id"], completed)
        replanned = False
        db.execute("SAVEPOINT task_replan")
        try:
            replan(local_today(), f"Cập nhật ngày hoàn thành phần việc {task['title']}",
                   preserve_overdue=True)
            replanned = True
        except ValueError:
            db.execute("ROLLBACK TO task_replan")
        finally:
            db.execute("RELEASE task_replan")
        db.commit()
        flash(("Đã cập nhật ngày hoàn thành phần việc." if already_done else
               "Đã ghi nhận hoàn thành phần việc.") +
              (" Đã tính lại hạn của các việc còn lại." if replanned else
               " Hãy xử lý các mục quá hạn hoặc tăng giờ học để tính lại hạn."), "success")
    except (ValueError, KeyError) as exc:
        db.rollback()
        flash(str(exc), "error")
    return return_after_completion(lecture["course_id"])


@bp.post("/tasks/<int:task_id>/miss")
def miss_task(task_id):
    db = get_db()
    task = required_row("lecture_tasks", task_id)
    lecture = required_row("lectures", task["lecture_id"])
    try:
        if task["status"] == "Hoàn thành" or not task["deadline"]:
            raise ValueError("Phần việc không có hạn đang chờ xử lý")
        if task["deadline"] > local_today().isoformat():
            raise ValueError("Chỉ ghi trễ hạn khi đến ngày hoàn thành dự kiến")
        reason = request.form.get("reason", "").strip()
        if not reason:
            raise ValueError("Vui lòng ghi lý do chưa hoàn thành")
        old = task["deadline"]
        db.execute("""INSERT INTO missed_deadlines
            (lecture_id, task_id, old_deadline, reason) VALUES (?, ?, ?, ?)""",
            (lecture["id"], task_id, old, reason))
        db.execute("UPDATE lecture_tasks SET deadline=NULL, manual_deadline=NULL WHERE id=?", (task_id,))
        # The old lesson date cannot remain fixed when one of its parts is
        # explicitly postponed beyond it. Record why the lesson constraint moved.
        if lecture['manual_deadline'] and lecture['manual_deadline'] <= local_today().isoformat():
            db.execute('UPDATE lectures SET manual_deadline=NULL WHERE id=?', (lecture['id'],))
            db.execute("""INSERT INTO schedule_events
                (course_id,lecture_id,old_deadline,reason,kind)
                VALUES (?,?,?,?,'bỏ hạn thủ công')""",
                (lecture['course_id'], lecture['id'], lecture['deadline'], reason))
        replan(local_today(), f"Phần việc trễ hạn: {reason}", preserve_overdue=True)
        db.commit()
        flash("Đã ghi lý do và lùi hạn phần việc.", "success")
    except (ValueError, KeyError) as exc:
        db.rollback()
        flash(str(exc), "error")
    return return_to_schedule()


@bp.route("/courses", methods=["GET", "POST"])
def courses():
    db = get_db()
    user_id = current_user_id()
    if request.method == "POST":
        try:
            name = request.form["name"].strip()
            if not name:
                raise ValueError("Vui lòng nhập tên khóa học")
            start = parse_date(request.form.get("start_date", ""), True)
            description = request.form.get("description", "").strip()
            db.execute("""INSERT OR IGNORE INTO course_catalogs
                (name, description, created_by) VALUES (?, ?, ?)""",
                (name, description, user_id))
            catalog = db.execute("SELECT id FROM course_catalogs WHERE name=?", (name,)).fetchone()
            course_id = enroll_catalog_course(user_id, catalog["id"])
            db.execute("""UPDATE courses SET start_date=?, notes=?, estimated_hours=?,
                description=?, display_name=? WHERE id=?""",
                (start, request.form.get("notes", "").strip(),
                 workload_hours(request.form.get("estimated_hours", "0")),
                 description, name, course_id))
            refresh_schedule("Thêm khóa học")
            db.commit()
            flash("Đã thêm khóa học.", "success")
            return redirect(url_for("main.courses"))
        except (ValueError, KeyError) as exc:
            db.rollback()
            flash(str(exc), "error")
        except sqlite3.IntegrityError:
            db.rollback()
            flash("Tên khóa học này đã tồn tại.", "error")
    memberships = {}
    for row in db.execute("""SELECT pc.course_id, p.name FROM plan_courses pc
        JOIN study_plans p ON p.id=pc.plan_id WHERE p.owner_user_id=?
        ORDER BY p.priority, p.id""", (user_id,)):
        memberships.setdefault(row["course_id"], []).append(row["name"])
    catalog = db.execute("""SELECT cc.* FROM course_catalogs cc WHERE NOT EXISTS
        (SELECT 1 FROM courses c WHERE c.catalog_course_id=cc.id AND c.owner_user_id=?)
        ORDER BY cc.name""", (user_id,)).fetchall()
    return render_template("courses.html", courses=courses_with_progress(),
                           memberships=memberships, catalog=catalog)


@bp.post("/catalog/<int:catalog_id>/enroll")
def enroll_course(catalog_id):
    try:
        course_id = enroll_catalog_course(current_user_id(), catalog_id)
        get_db().commit()
        flash("Đã thêm khóa học chung vào danh mục của bạn.", "success")
        return redirect(url_for("main.course_detail", course_id=course_id))
    except ValueError as exc:
        get_db().rollback()
        flash(str(exc), "error")
        return redirect(url_for("main.courses"))


@bp.route("/courses/<int:course_id>", methods=["GET", "POST"])
def course_detail(course_id):
    db = get_db()
    course = required_row("courses", course_id)
    if request.method == "POST":
        try:
            name = request.form["name"].strip()
            status = request.form["status"]
            try:
                progress = int(request.form.get("progress", "0"))
            except ValueError as exc:
                raise ValueError("Tiến độ phải là số từ 0 đến 100") from exc
            if not name or status not in STATUSES or not 0 <= progress <= 100:
                raise ValueError("Kiểm tra tên, trạng thái và tiến độ (0–100).")
            start = course["start_date"]
            estimated = workload_hours(request.form.get("estimated_hours", "0"))
            change_reason = request.form.get("change_reason", "").strip()
            if estimated != course["estimated_hours"] and not change_reason:
                raise ValueError("Hãy ghi lý do khi đổi số giờ còn cần học")
            if status == "Hoàn thành" and db.execute("""SELECT 1 FROM lectures
                WHERE course_id=? AND status!='Hoàn thành' LIMIT 1""", (course_id,)).fetchone():
                raise ValueError("Hãy hoàn thành các bài học trước khi đánh dấu xong khóa học")
            db.execute("""UPDATE courses SET display_name=?, description=?, start_date=?,
                status=?, progress=?, notes=?, estimated_hours=? WHERE id=?""",
                (name, request.form.get("description", "").strip(), start, status,
                 progress, request.form.get("notes", "").strip(), estimated, course_id))
            if status == "Hoàn thành":
                db.execute("DELETE FROM schedule_allocations WHERE course_id=? AND study_date>=?",
                           (course_id, local_today().isoformat()))
            refresh_schedule(change_reason or f"Cập nhật khóa học {name}")
            db.commit()
            flash("Đã cập nhật khóa học.", "success")
            return redirect(url_for("main.course_detail", course_id=course_id))
        except (ValueError, KeyError) as exc:
            db.rollback()
            flash(str(exc), "error")
        except sqlite3.IntegrityError:
            db.rollback()
            flash("Tên khóa học này đã tồn tại.", "error")
    lectures = db.execute("SELECT * FROM lectures WHERE course_id=? ORDER BY lecture_number",
                          (course_id,)).fetchall()
    parts_by_lecture = {}
    for part in db.execute("""SELECT t.* FROM lecture_tasks t
        JOIN lectures l ON l.id=t.lecture_id WHERE l.course_id=?
        ORDER BY l.lecture_number, t.position""", (course_id,)):
        parts_by_lecture.setdefault(part["lecture_id"], []).append(part)
    course_summary = next(item for item in courses_with_progress() if item["id"] == course_id)
    return render_template("course_detail.html", course=course_summary, lectures=lectures,
                           display_progress=course_summary["display_progress"], statuses=STATUSES,
                           parts_by_lecture=parts_by_lecture, task_types=TASK_TYPES,
                           today=local_today().isoformat(),
                           completion_events=db.execute("""SELECT e.*, l.lecture_number,
                               t.title AS task_title FROM completion_events e
                               JOIN lectures l ON l.id=e.lecture_id
                               LEFT JOIN lecture_tasks t ON t.id=e.task_id
                               WHERE e.course_id=? ORDER BY e.id DESC LIMIT 30""",
                               (course_id,)).fetchall(),
                           member_plans=db.execute("""SELECT p.* FROM plan_courses pc
                               JOIN study_plans p ON p.id=pc.plan_id
                               WHERE pc.course_id=? ORDER BY p.priority, p.id""",
                               (course_id,)).fetchall())


@bp.post("/courses/<int:course_id>/start-date")
def update_course_start_date(course_id):
    db = get_db()
    course = required_row("courses", course_id)
    try:
        start = parse_date(request.form.get("start_date", ""), True)
        changed_start = start != course["start_date"]
        old_lecture_deadlines = {row["id"]: row["deadline"] for row in db.execute(
            "SELECT id, deadline FROM lectures WHERE course_id=? AND status!='Hoàn thành'",
            (course_id,))}
        old_task_deadlines = {row["id"]: row["deadline"] for row in db.execute("""
            SELECT t.id, t.deadline FROM lecture_tasks t
            JOIN lectures l ON l.id=t.lecture_id
            WHERE l.course_id=? AND t.status!='Hoàn thành'""", (course_id,))}
        reason = (f"Đổi ngày bắt đầu khóa học {course['display_name'] or course['name']}: "
                  f"{course['start_date'] or 'chưa đặt'} → {start or 'chưa đặt'}")
        db.execute("UPDATE courses SET start_date=? WHERE id=?", (start, course_id))
        if changed_start:
            for row in db.execute("""SELECT id, deadline FROM lectures
                WHERE course_id=? AND status!='Hoàn thành' AND manual_deadline IS NOT NULL""",
                                  (course_id,)).fetchall():
                db.execute("""INSERT INTO schedule_events
                    (course_id, lecture_id, old_deadline, reason, kind)
                    VALUES (?, ?, ?, ?, 'bỏ hạn thủ công')""",
                    (course_id, row["id"], row["deadline"], reason))
            for row in db.execute("""SELECT t.id, t.lecture_id, t.deadline FROM lecture_tasks t
                JOIN lectures l ON l.id=t.lecture_id WHERE l.course_id=?
                AND t.status!='Hoàn thành' AND t.manual_deadline IS NOT NULL""",
                                  (course_id,)).fetchall():
                db.execute("""INSERT INTO schedule_events
                    (course_id, lecture_id, task_id, old_deadline, reason, kind)
                    VALUES (?, ?, ?, ?, ?, 'bỏ hạn thủ công')""",
                    (course_id, row["lecture_id"], row["id"], row["deadline"], reason))
            db.execute("""UPDATE lectures SET manual_deadline=NULL
                WHERE course_id=? AND status!='Hoàn thành'""", (course_id,))
            db.execute("""UPDATE lecture_tasks SET manual_deadline=NULL
                WHERE status!='Hoàn thành' AND lecture_id IN
                (SELECT id FROM lectures WHERE course_id=?)""", (course_id,))
        planning_start = min(
            [local_today()] + [date.fromisoformat(value)
                              for value in (course["start_date"], start) if value])
        replan(planning_start, reason,
               preserve_overdue=True, recalculate_overdue_course_id=course_id)
        lecture_changes = sum(row["deadline"] != old_lecture_deadlines[row["id"]]
                              for row in db.execute("""SELECT id, deadline FROM lectures
                                  WHERE course_id=? AND status!='Hoàn thành'""", (course_id,)))
        task_changes = sum(row["deadline"] != old_task_deadlines[row["id"]]
                           for row in db.execute("""SELECT t.id, t.deadline FROM lecture_tasks t
                               JOIN lectures l ON l.id=t.lecture_id WHERE l.course_id=?
                               AND t.status!='Hoàn thành'""", (course_id,)))
        active_plan = db.execute("""SELECT 1 FROM plan_courses pc
            JOIN study_plans p ON p.id=pc.plan_id
            WHERE pc.course_id=? AND p.status='Đang hoạt động' LIMIT 1""",
            (course_id,)).fetchone()
        missed_count = sum(item["course_id"] == course_id for item in pending_missed())
        db.commit()
        if not active_plan:
            flash("Đã lưu ngày bắt đầu. Hãy thêm khóa học vào một kế hoạch đang hoạt động để tính hạn bài học.", "success")
        elif lecture_changes or task_changes:
            flash(f"Đã tính lại lịch và hạn: {lecture_changes} bài học, {task_changes} phần việc."
                  + (f" Có {missed_count} mục quá hạn cần ghi lý do trên lịch học."
                     if missed_count else ""), "success")
        else:
            flash("Đã tính lại lịch; hạn của các bài học chưa hoàn thành không đổi."
                  + (f" Có {missed_count} mục quá hạn cần ghi lý do trên lịch học."
                     if missed_count else ""), "success")
    except (ValueError, KeyError) as exc:
        db.rollback()
        flash(str(exc), "error")
    return redirect(url_for("main.course_detail", course_id=course_id))


@bp.route("/courses/<int:course_id>/delete", methods=["GET", "POST"])
def delete_course(course_id):
    db = get_db()
    course = required_row("courses", course_id)
    counts = {
        "lectures": db.execute("SELECT COUNT(*) FROM lectures WHERE course_id=?",
                               (course_id,)).fetchone()[0],
        "tasks": db.execute("""SELECT COUNT(*) FROM lecture_tasks t
            JOIN lectures l ON l.id=t.lecture_id WHERE l.course_id=?""",
            (course_id,)).fetchone()[0],
        "journals": db.execute("SELECT COUNT(*) FROM journals WHERE course_id=?",
                               (course_id,)).fetchone()[0],
        "allocations": db.execute("SELECT COUNT(*) FROM schedule_allocations WHERE course_id=?",
                                  (course_id,)).fetchone()[0],
    }
    if request.method == "POST":
        course_name = course["display_name"] or course["name"]
        if request.form.get("course_name", "").strip() != course_name:
            flash("Tên xác nhận chưa khớp. Khóa học chưa bị xóa.", "error")
        else:
            try:
                backup_path = backup_database(f"course-{course_id}")
                lecture_scope = 'SELECT id FROM lectures WHERE course_id=?'
                for table in ('missed_deadlines', 'lecture_tasks'):
                    db.execute(f'DELETE FROM {table} WHERE lecture_id IN ({lecture_scope})', (course_id,))
                for table in ('schedule_allocations', 'schedule_events', 'completion_events', 'plan_courses'):
                    db.execute(f'DELETE FROM {table} WHERE course_id=?', (course_id,))
                db.execute('DELETE FROM lectures WHERE course_id=?', (course_id,))
                db.execute("DELETE FROM journals WHERE course_id=?", (course_id,))
                db.execute("DELETE FROM courses WHERE id=?", (course_id,))
                replan(local_today(), f"Xóa khóa học {course_name}", preserve_overdue=True)
                db.commit()
            except (OSError, sqlite3.Error, ValueError) as exc:
                db.rollback()
                flash(f"Không thể xóa khóa học: {exc}", "error")
            else:
                flash(f"Đã xóa {course_name}. Bản sao lưu: {backup_path.name}", "success")
                return redirect(url_for("main.courses"))
    return render_template("course_delete.html", course=course, counts=counts)


@bp.post("/courses/<int:course_id>/lectures")
def add_lecture(course_id):
    course = required_row("courses", course_id)
    try:
        insert_before = request.form.get("insert_before", "").strip()
        number = int(insert_before or request.form["lecture_number"])
        inherited_deadline = None
        if number < 1:
            raise ValueError("Số bài học phải lớn hơn 0")
        if insert_before:
            rows = get_db().execute("""SELECT id, lecture_number, title, deadline FROM lectures
                WHERE course_id=? AND lecture_number>=? ORDER BY lecture_number DESC""",
                (course_id, number)).fetchall()
            if not rows or rows[-1]["lecture_number"] != number:
                raise ValueError("Không tìm thấy bài học tại vị trí cần chèn")
            inherited_deadline = rows[-1]["deadline"]
            for row in rows:
                new_number = row["lecture_number"] + 1
                title = (f"Bài {new_number}" if row["title"] == f"Bài {row['lecture_number']}"
                         else row["title"])
                get_db().execute("UPDATE lectures SET lecture_number=?, title=? WHERE id=?",
                                 (new_number, title, row["id"]))
        lecture_id = get_db().execute("""INSERT INTO lectures
            (course_id, lecture_number, title, estimated_hours, remaining_hours, content,
             deadline, manual_deadline)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (course_id, number, request.form.get("title", "").strip(), 0, 0,
             request.form.get("content", "").strip(), inherited_deadline,
             inherited_deadline)).lastrowid
        if insert_before and inherited_deadline:
            get_db().execute("""INSERT INTO schedule_events
                (course_id, lecture_id, old_deadline, new_deadline, reason, kind)
                VALUES (?, ?, NULL, ?, ?, 'kế thừa hạn')""",
                (course_id, lecture_id, inherited_deadline,
                 f"Bài mới kế thừa hạn của Bài {number} trước khi chèn"))
        get_db().execute("""UPDATE courses SET status='Đang học'
            WHERE id=? AND status='Hoàn thành'""", (course_id,))
        # A structural edit must only redistribute unfinished work from today.
        # Replanning from the original course start date can place open work back
        # into past capacity and immediately make its newly calculated deadline overdue.
        planning_start = local_today()
        replan(planning_start,
               f"{'Chèn' if insert_before else 'Thêm'} bài {number}",
               preserve_overdue=True,
               recalculate_overdue_course_id=course_id if insert_before else None)
        get_db().commit()
        flash((f"Đã chèn bài mới ở vị trí {number}, kế thừa hạn "
               f"{inherited_deadline or 'chưa được đặt'}; các bài từ vị trí này đã được dời xuống."
               if insert_before else "Đã thêm bài học."), "success")
    except (ValueError, KeyError, sqlite3.IntegrityError) as exc:
        get_db().rollback()
        flash(str(exc) if isinstance(exc, ValueError)
              else "Nhập số bài học lớn hơn 0 và chưa tồn tại.", "error")
    return redirect(url_for("main.course_detail", course_id=course_id))


@bp.post("/lectures/<int:lecture_id>/delete")
def delete_lecture(lecture_id):
    db = get_db()
    lecture = required_row("lectures", lecture_id)
    course = required_row("courses", lecture["course_id"])
    if request.form.get("confirmation") != "delete":
        flash("Bài học chưa bị xóa vì thiếu xác nhận.", "error")
        return redirect(url_for("main.course_detail", course_id=course["id"]))
    task_count = db.execute(
        "SELECT COUNT(*) FROM lecture_tasks WHERE lecture_id=?", (lecture_id,)).fetchone()[0]
    label = f"Bài {lecture['lecture_number']}"
    if lecture["title"] and lecture["title"] != label:
        label += f" · {lecture['title']}"
    try:
        backup_path = backup_database(f"lecture-{lecture_id}")
        # Remote libSQL may not enforce FK cascades: delete all children before
        # their lesson so no orphan allocations can consume today's capacity.
        for table in ("schedule_allocations", "schedule_events", "completion_events",
                      "missed_deadlines", "lecture_tasks"):
            db.execute(f"DELETE FROM {table} WHERE lecture_id=?", (lecture_id,))
        db.execute("DELETE FROM lectures WHERE id=?", (lecture_id,))
        later = db.execute("""SELECT id, lecture_number, title FROM lectures
            WHERE course_id=? AND lecture_number>? ORDER BY lecture_number""",
            (course["id"], lecture["lecture_number"])).fetchall()
        for row in later:
            new_number = row["lecture_number"] - 1
            title = (f"Bài {new_number}" if row["title"] == f"Bài {row['lecture_number']}"
                     else row["title"])
            db.execute("UPDATE lectures SET lecture_number=?, title=? WHERE id=?",
                       (new_number, title, row["id"]))
        remaining = db.execute(
            "SELECT COUNT(*) FROM lectures WHERE course_id=?", (course["id"],)).fetchone()[0]
        if remaining == 0:
            db.execute("""UPDATE courses SET status='Chưa bắt đầu', progress=0,
                target_date=NULL WHERE id=?""", (course["id"],))
        db.execute("""INSERT INTO schedule_events
            (course_id, lecture_id, old_deadline, new_deadline, reason, kind)
            VALUES (?, NULL, ?, NULL, ?, 'xóa bài học')""",
            (course["id"], lecture["deadline"],
             f"Xóa {label} cùng {task_count} phần việc; đã dồn số thứ tự các bài phía sau"))
        replan(local_today(), f"Xóa {label}", preserve_overdue=True)
        db.commit()
    except (OSError, sqlite3.Error, ValueError) as exc:
        db.rollback()
        flash(f"Không thể xóa bài học: {exc}", "error")
    else:
        flash(f"Đã xóa {label} và {task_count} phần việc. "
              f"Bản sao lưu: {backup_path.name}", "success")
    return redirect(url_for("main.course_detail", course_id=course["id"]))


@bp.post("/lectures/<int:lecture_id>")
def update_lecture(lecture_id):
    lecture = required_row("lectures", lecture_id)
    try:
        score = optional_score(request.form.get("understanding_score", ""))
        get_db().execute("""UPDATE lectures SET title=?, understanding_score=?, notes=?,
            content=? WHERE id=?""",
            (request.form.get("title", "").strip(), score,
             request.form.get("notes", "").strip(),
             request.form.get("content", "").strip(), lecture_id))
        sync_lecture_hours(get_db(), lecture_id)
        get_db().commit()
        flash("Đã cập nhật thông tin bài học. Số giờ vẫn được tính tự động từ các phần việc.",
              "success")
    except (ValueError, KeyError) as exc:
        get_db().rollback()
        flash(str(exc), "error")
    return redirect(url_for("main.course_detail", course_id=lecture["course_id"]))


def journal_values():
    day = parse_date(request.form["date"])
    try:
        course_id = int(request.form["course_id"])
    except ValueError as exc:
        raise ValueError("Khóa học không hợp lệ") from exc
    required_row("courses", course_id)
    try:
        hours = float(request.form["hours"])
    except ValueError as exc:
        raise ValueError("Số giờ phải là số") from exc
    if not 0 < hours <= 24:
        raise ValueError("Số giờ phải lớn hơn 0 và không quá 24")
    difficulty = request.form.get("difficulty", "Trung bình")
    if difficulty not in DIFFICULTIES:
        raise ValueError("Mức độ khó không hợp lệ")
    return (day, course_id, request.form.get("topic", "").strip(),
            request.form.get("lecture", "").strip(), hours, difficulty,
            optional_score(request.form.get("understanding", "")),
            request.form.get("notes", "").strip(), request.form.get("problems", "").strip(),
            request.form.get("next_plan", "").strip())


@bp.route("/journal", methods=["GET", "POST"])
def journal():
    db = get_db()
    if request.method == "POST":
        try:
            values = journal_values()
            db.execute("""INSERT INTO journals
                (date, course_id, topic, lecture, hours, difficulty, understanding, notes,
                 problems, next_plan, user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*values, current_user_id()))
            db.commit()
            flash("Đã lưu nhật ký học tập.", "success")
            return redirect(url_for("main.journal"))
        except (ValueError, KeyError) as exc:
            flash(f"Kiểm tra thông tin nhật ký: {exc}", "error")
    entries = db.execute("""SELECT j.*, c.display_name AS course_name FROM journals j
        JOIN courses c ON c.id=j.course_id WHERE j.user_id=?
        ORDER BY j.date DESC, j.id DESC""", (current_user_id(),)).fetchall()
    return render_template("journal.html", courses=courses_with_progress(), entries=entries,
                           today=local_today().isoformat(), difficulties=DIFFICULTIES)


@bp.route("/journal/<int:entry_id>", methods=["GET", "POST"])
def journal_edit(entry_id):
    entry = required_row("journals", entry_id)
    if request.method == "POST":
        try:
            values = journal_values()
            get_db().execute("""UPDATE journals SET date=?, course_id=?, topic=?, lecture=?, hours=?,
                difficulty=?, understanding=?, notes=?, problems=?, next_plan=? WHERE id=?""",
                (*values, entry_id))
            get_db().commit()
            flash("Đã cập nhật nhật ký.", "success")
            return redirect(url_for("main.journal"))
        except (ValueError, KeyError) as exc:
            flash(f"Kiểm tra thông tin nhật ký: {exc}", "error")
    return render_template("journal_edit.html", entry=entry, courses=courses_with_progress(),
                           difficulties=DIFFICULTIES)


@bp.post("/journal/<int:entry_id>/delete")
def journal_delete(entry_id):
    required_row("journals", entry_id)
    get_db().execute("DELETE FROM journals WHERE id=?", (entry_id,))
    get_db().commit()
    flash("Đã xóa nhật ký.", "success")
    return redirect(url_for("main.journal"))


@bp.route("/reviews", methods=["GET", "POST"])
def reviews():
    db = get_db()
    user_id = current_user_id()
    today = local_today()
    period = request.args.get("period", "week")
    if period not in ("week", "month"):
        abort(400)
    try:
        selected = parse_date(request.args.get("date", today.isoformat()))
    except ValueError:
        abort(400)
    selected_day = date.fromisoformat(selected)
    start = (selected_day - timedelta(days=selected_day.weekday()) if period == "week"
             else selected_day.replace(day=1))
    end = (start + timedelta(days=6) if period == "week"
           else (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1))
    if request.method == "POST":
        db.execute("""INSERT INTO user_reviews
            (user_id, period, period_start, strength, weakness, action_plan)
            VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, period, period_start) DO UPDATE SET
            strength=excluded.strength, weakness=excluded.weakness, action_plan=excluded.action_plan""",
            (user_id, period, start.isoformat(), request.form.get("strength", "").strip(),
             request.form.get("weakness", "").strip(), request.form.get("action_plan", "").strip()))
        db.commit()
        flash("Đã lưu phần nhìn lại.", "success")
        return redirect(url_for("main.reviews", period=period, date=selected))
    review = db.execute("""SELECT * FROM user_reviews
        WHERE user_id=? AND period=? AND period_start=?""",
        (user_id, period, start.isoformat())).fetchone()
    data = report_data()
    summary = period_summary(data["journals"], start.isoformat(), end.isoformat())
    work = work_summary(completed_work(), start.isoformat(), end.isoformat())
    entries = [row for row in data["journals"] if start.isoformat() <= row["date"] <= end.isoformat()]
    monthly = None
    if period == "month":
        mastered = list(dict.fromkeys(row["topic"] for row in entries
                                      if row["topic"] and row["understanding"] is not None
                                      and row["understanding"] >= 8))
        weak = list(dict.fromkeys(row["topic"] or row["problems"] for row in entries
                                  if row["problems"] or (row["understanding"] is not None
                                                          and row["understanding"] <= 5)))
        weeks = (end - start).days / 7 + 1 / 7
        # A finished course stores its finishing day in target_date.
        monthly = {"completed": [c["name"] for c in data["courses"]
                                 if c["status"] == "Hoàn thành" and c["target_date"]
                                 and start.isoformat() <= c["target_date"] <= end.isoformat()],
                   "mastered": mastered, "weak": weak,
                   "velocity": round(work["hours"] / weeks, 1),
                   "recommendation": (f"Luyện thêm chủ đề {weak[0]}." if weak else
                                      "Tiếp tục ghi nhật ký và đặt mục tiêu cụ thể cho buổi tiếp theo.")}
    previous = start - timedelta(days=1)
    following = end + timedelta(days=1)
    return render_template("reviews.html", period=period, selected=selected,
                           start=start, end=end, summary=summary, work=work, review=review,
                           previous=previous, following=following, monthly=monthly)


@bp.post("/export")
def export():
    try:
        ensure_plan()
    except ValueError:
        get_db().rollback()
    files = build_export(current_app.config["EXPORT_DIR"])
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    output.seek(0)
    return send_file(output, mimetype="application/zip", as_attachment=True,
                     download_name=f"lich-ke-hoach-{local_today().isoformat()}.zip")
