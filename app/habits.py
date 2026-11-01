"""Recurring check-ins, independent of the hourly study scheduler."""
import json
from datetime import date, timedelta

from flask import abort, flash, redirect, render_template, request, url_for

from .auth import current_user_id
from .db import get_db
from .timezone import local_today


WEEKDAYS = ("Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật")
STATUSES = {"pending": "Chưa đánh dấu", "done": "Đã làm",
            "missed": "Không làm", "skipped": "Nghỉ / bỏ qua"}


def habits_for_plans(plan_ids):
    """Caller supplies only plans whose visibility has already been checked."""
    if not plan_ids:
        return []
    marks = ",".join("?" for _ in plan_ids)
    rows = get_db().execute(f"""SELECT h.*, p.name AS plan_name, p.status AS plan_status,
        p.owner_user_id, u.username AS owner_name FROM habits h
        JOIN study_plans p ON p.id=h.plan_id JOIN users u ON u.id=p.owner_user_id
        WHERE p.kind='habit' AND p.id IN ({marks}) ORDER BY p.priority, p.id, h.id""", plan_ids)
    result = []
    for row in rows:
        habit = dict(row)
        habit["weekdays"] = json.loads(habit["weekdays_json"])
        habit["excluded_dates"] = json.loads(habit["excluded_dates_json"])
        habit["repeat_label"] = ("Mỗi ngày" if len(habit["weekdays"]) == 7 else
                                 ", ".join(WEEKDAYS[d] for d in habit["weekdays"]))
        result.append(habit)
    return result


def is_due(habit, day):
    return (habit["start_date"] <= day.isoformat()
            and (not habit["end_date"] or day.isoformat() <= habit["end_date"])
            and day.weekday() in habit["weekdays"]
            and day.isoformat() not in habit.get("excluded_dates", []))


def occurrences(plan_ids, start, end, active_only=False):
    """Build a bounded calendar; recorded results remain visible while paused."""
    if end < start or (end - start).days > 366:
        raise ValueError("Chỉ xem tối đa một năm mỗi lần")
    habits = habits_for_plans(plan_ids)
    if not habits:
        return []
    marks = ",".join("?" for _ in habits)
    entries = {(r["habit_id"], r["entry_date"]): dict(r) for r in get_db().execute(
        f"""SELECT * FROM habit_entries WHERE habit_id IN ({marks})
        AND entry_date BETWEEN ? AND ?""", [h["id"] for h in habits] +
        [start.isoformat(), end.isoformat()])}
    result = []
    day = start
    today = local_today()
    while day <= end:
        for habit in habits:
            entry = entries.get((habit["id"], day.isoformat()))
            scheduled = is_due(habit, day)
            if active_only and habit["plan_status"] != "Đang hoạt động":
                scheduled = False
            if not scheduled and not entry:
                continue
            result.append(dict(habit, date=day.isoformat(),
                               status=entry["status"] if entry else "pending",
                               note=entry["note"] if entry else "",
                               editable=habit["owner_user_id"] == current_user_id() and day <= today,
                               future=day > today))
        day += timedelta(days=1)
    return result


def summary(items):
    """Only elapsed scheduled days count; explicitly skipped days are excused."""
    eligible = [item for item in items if not item["future"]]
    counts = {status: sum(item["status"] == status for item in eligible) for status in STATUSES}
    total = len(eligible) - counts["skipped"]
    return dict(counts, total=total,
                percent=round(100 * counts["done"] / total) if total else None)


def current_streak(habit, today=None):
    """Consecutive scheduled sessions: off-days and excused rests do not break it."""
    today = today or local_today()
    entries = {r["entry_date"]: r["status"] for r in get_db().execute(
        "SELECT entry_date, status FROM habit_entries WHERE habit_id=? AND entry_date<=?",
        (habit["id"], today.isoformat()))}
    day = min(today, date.fromisoformat(habit["end_date"]) if habit["end_date"] else today)
    start = date.fromisoformat(habit["start_date"])
    count = 0
    while day >= start:
        if is_due(habit, day):
            state = entries.get(day.isoformat(), "pending")
            if state == "done":
                count += 1
            elif state != "skipped" and not (day == today and state == "pending"):
                break
        day -= timedelta(days=1)
    return count


def selected_day():
    try:
        day = date.fromisoformat(request.args.get("date", local_today().isoformat()))
        if not 1901 <= day.year <= 9998:
            raise ValueError
        return day
    except ValueError:
        abort(400)


def render_plan(plan, readonly=False):
    day = selected_day()
    week_start = day - timedelta(days=day.weekday())
    week_end = week_start + timedelta(days=6)
    month_start = day.replace(day=1)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    month_end = next_month - timedelta(days=1)
    habits = habits_for_plans([plan["id"]])
    for habit in habits:
        habit["streak"] = current_streak(habit)
    week_items = occurrences([plan["id"]], week_start, week_end)
    return render_template("habit_plan.html", plan=plan, readonly=readonly, habits=habits,
                           selected=day, today=local_today(), weekdays=WEEKDAYS,
                           habit_statuses=STATUSES, day_habits=occurrences([plan["id"]], day, day),
                           week_stats=summary(week_items),
                           month_stats=summary(occurrences([plan["id"]], month_start, month_end)),
                           week_days=[week_start + timedelta(days=i) for i in range(7)],
                           week_lookup={(i["id"], i["date"]): i for i in week_items},
                           previous=day-timedelta(days=1), following=day+timedelta(days=1),
                           shared_with=get_db().execute("""SELECT u.id,u.username,u.display_name
                               FROM plan_shares s JOIN users u ON u.id=s.user_id
                               WHERE s.plan_id=? ORDER BY u.username""", (plan["id"],)).fetchall())


def owned_habit(habit_id):
    row = get_db().execute("""SELECT h.* FROM habits h JOIN study_plans p ON p.id=h.plan_id
        WHERE h.id=? AND p.owner_user_id=? AND p.kind='habit'""",
        (habit_id, current_user_id())).fetchone()
    if row is None:
        abort(404)
    habit = dict(row)
    habit["weekdays"] = json.loads(habit["weekdays_json"])
    habit["excluded_dates"] = json.loads(habit["excluded_dates_json"])
    return habit


def add_habit(plan_id):
    plan = get_db().execute("SELECT * FROM study_plans WHERE id=? AND owner_user_id=? AND kind='habit'",
                            (plan_id, current_user_id())).fetchone()
    if plan is None:
        abort(404)
    try:
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        if not title or len(title) > 200 or len(description) > 2000:
            raise ValueError("Tên việc cần có 1–200 ký tự; mô tả tối đa 2.000 ký tự")
        weekdays = sorted(set(int(value) for value in request.form.getlist("weekday")))
        if not weekdays or any(d < 0 or d > 6 for d in weekdays):
            raise ValueError("Chọn ít nhất một ngày trong tuần")
        start = date.fromisoformat(request.form.get("start_date", ""))
        end_raw = request.form.get("end_date", "")
        end = date.fromisoformat(end_raw) if end_raw else None
        if start.year < 1901 or start.year > 9998 or (end and (end < start or end.year > 9998)):
            raise ValueError("Ngày kết thúc phải từ ngày bắt đầu trở đi")
        get_db().execute("""INSERT INTO habits (plan_id,title,description,weekdays_json,start_date,end_date)
            VALUES (?,?,?,?,?,?)""", (plan_id, title, description, json.dumps(weekdays),
                                      start.isoformat(), end.isoformat() if end else None))
        get_db().commit()
        flash("Đã thêm việc theo dõi. Bạn có thể đánh dấu từng ngày trên lịch.", "success")
    except (ValueError, KeyError) as exc:
        get_db().rollback()
        flash(str(exc), "error")
    return redirect(url_for("main.plan_detail", plan_id=plan_id))


def record_entry(habit_id):
    habit = owned_habit(habit_id)
    entry_date = request.form.get("date", "")
    try:
        day = date.fromisoformat(entry_date)
        if day > local_today() or not is_due(habit, day):
            raise ValueError("Chỉ đánh dấu hôm nay hoặc ngày đã qua trong lịch lặp của việc này")
        status = request.form.get("status", "")
        note = request.form.get("note", "").strip()
        if status not in STATUSES or len(note) > 2000:
            raise ValueError("Trạng thái không hợp lệ hoặc ghi chú dài quá 2.000 ký tự")
        if status == "pending":
            get_db().execute("DELETE FROM habit_entries WHERE habit_id=? AND entry_date=?",
                             (habit_id, day.isoformat()))
        else:
            get_db().execute("""INSERT INTO habit_entries (habit_id,entry_date,status,note)
                VALUES (?,?,?,?) ON CONFLICT(habit_id,entry_date) DO UPDATE SET
                status=excluded.status,note=excluded.note,updated_at=CURRENT_TIMESTAMP""",
                (habit_id, day.isoformat(), status, note))
        get_db().commit()
        flash("Đã lưu kết quả ngày " + day.isoformat() + ".", "success")
    except ValueError as exc:
        get_db().rollback()
        flash(str(exc), "error")
    # Only local destinations; preserve the calendar's plan filter after a check-in.
    if request.form.get("return_to") == "schedule":
        return redirect(url_for("main.schedule", date=entry_date or local_today().isoformat(),
                                **({"filter": 1, "plan_id": request.form.getlist("plan_id")}
                                   if request.form.get("filter") == "1" else {})))
    return redirect(url_for("main.plan_detail", plan_id=habit["plan_id"],
                            date=entry_date or local_today().isoformat()))


def end_habit(habit_id):
    habit = owned_habit(habit_id)
    try:
        end = date.fromisoformat(request.form.get("end_date", ""))
        if end.isoformat() < habit["start_date"]:
            raise ValueError("Ngày kết thúc không thể trước ngày bắt đầu")
        latest = get_db().execute("SELECT MAX(entry_date) FROM habit_entries WHERE habit_id=?",
                                  (habit_id,)).fetchone()[0]
        if latest and end.isoformat() < latest:
            raise ValueError("Ngày kết thúc không thể trước kết quả đã ghi nhận")
        get_db().execute("UPDATE habits SET end_date=? WHERE id=?", (end.isoformat(), habit_id))
        get_db().commit()
        flash("Đã đặt ngày kết thúc theo dõi; lịch sử được giữ lại.", "success")
    except ValueError as exc:
        get_db().rollback()
        flash(str(exc), "error")
    return redirect(url_for("main.plan_detail", plan_id=habit["plan_id"]))


def register_routes(bp):
    bp.add_url_rule("/plans/<int:plan_id>/habits", "add_habit", add_habit, methods=["POST"])
    bp.add_url_rule("/habits/<int:habit_id>/entries", "record_habit", record_entry, methods=["POST"])
    bp.add_url_rule("/habits/<int:habit_id>/end", "end_habit", end_habit, methods=["POST"])
