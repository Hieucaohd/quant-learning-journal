import json
from collections import defaultdict
from datetime import date, timedelta

from .auth import current_user_id
from .db import get_db


def capacity_on(day, profiles=None, overrides=None):
    db = get_db()
    user_id = current_user_id()
    if profiles is None:
        profiles = [dict(row) for row in db.execute(
            "SELECT * FROM user_capacity_profiles WHERE user_id=? ORDER BY effective_from",
            (user_id,))]
    if overrides is None:
        overrides = {row["study_date"]: row["hours"] for row in db.execute(
            "SELECT study_date, hours FROM user_capacity_overrides WHERE user_id=?",
            (user_id,))}
    key = day.isoformat()
    if key in overrides:
        return overrides[key]
    profile = next((row for row in reversed(profiles) if row["effective_from"] <= key), profiles[0])
    return json.loads(profile["hours_json"])[day.weekday()]


def course_deadlines():
    return {row["id"]: row["target_date"] for row in get_db().execute(
        "SELECT id, target_date FROM courses WHERE owner_user_id=? AND target_date IS NOT NULL",
        (current_user_id(),))}


def active_course_ids():
    rows = get_db().execute("""SELECT pc.course_id FROM plan_courses pc
        JOIN study_plans p ON p.id=pc.plan_id WHERE p.status='Đang hoạt động'
        AND p.owner_user_id=? ORDER BY p.priority, p.id, pc.position, pc.course_id""",
        (current_user_id(),))
    return list(dict.fromkeys(row["course_id"] for row in rows))


def course_plan_owners():
    rows = get_db().execute("""SELECT pc.course_id, pc.plan_id FROM plan_courses pc
        JOIN study_plans p ON p.id=pc.plan_id WHERE p.status='Đang hoạt động'
        AND p.owner_user_id=? ORDER BY p.priority, p.id, pc.position, pc.course_id""",
        (current_user_id(),))
    owners = {}
    for row in rows:
        owners.setdefault(row["course_id"], row["plan_id"])
    return owners


def pending_missed(today=None):
    today = today or date.today()
    db = get_db()
    lecture_rows = [dict(row, task_id=None, part_title=None) for row in db.execute(
        """SELECT l.*, c.display_name AS course_name FROM lectures l JOIN courses c ON c.id=l.course_id
        WHERE l.status != 'Hoàn thành' AND l.remaining_hours > 0 AND l.deadline < ? AND
        EXISTS (SELECT 1 FROM plan_courses pc JOIN study_plans p ON p.id=pc.plan_id
                WHERE pc.course_id=l.course_id AND p.status='Đang hoạt động'
                AND p.owner_user_id=?) AND
        NOT EXISTS (SELECT 1 FROM lecture_tasks t WHERE t.lecture_id=l.id)""",
        (today.isoformat(), current_user_id()))]
    task_rows = []
    for row in db.execute("""SELECT t.id AS task_id, t.title AS part_title,
        t.deadline, l.id, l.course_id, l.lecture_number, l.title, c.display_name AS course_name
        FROM lecture_tasks t JOIN lectures l ON l.id=t.lecture_id
        JOIN courses c ON c.id=l.course_id
        WHERE t.status != 'Hoàn thành' AND t.deadline < ? AND
        EXISTS (SELECT 1 FROM plan_courses pc JOIN study_plans p ON p.id=pc.plan_id
                WHERE pc.course_id=l.course_id AND p.status='Đang hoạt động'
                AND p.owner_user_id=?)""",
                          (today.isoformat(), current_user_id())):
        task_rows.append(dict(row))
    return sorted(lecture_rows + task_rows,
                  key=lambda row: (row["deadline"], row["course_id"], row["lecture_number"]))


def ensure_plan(today=None):
    db = get_db()
    if not db.execute("""SELECT 1 FROM schedule_allocations a JOIN courses c ON c.id=a.course_id
        WHERE c.owner_user_id=? LIMIT 1""", (current_user_id(),)).fetchone():
        replan(today or date.today(), "Lập lịch học ban đầu")
        db.commit()


def replan(start=None, reason="Điều chỉnh lịch học", preserve_overdue=False,
           recalculate_overdue_course_id=None):
    """Allocate remaining lecture parts, then derive each lecture's final deadline."""
    db = get_db()
    start = start or date.today()
    overdue = pending_missed(date.today())
    if overdue and not preserve_overdue:
        raise ValueError("Hãy ghi lý do trễ hạn cho từng phần việc quá hạn trước khi lập lại lịch.")
    overdue_task_ids = {row["task_id"] for row in overdue if row["task_id"] is not None}
    overdue_lecture_ids = {row["id"] for row in overdue if row["task_id"] is None}
    start_key = start.isoformat()
    user_id = current_user_id()
    courses = [dict(row) for row in db.execute(
        "SELECT * FROM courses WHERE owner_user_id=? ORDER BY id", (user_id,))]
    course_by_id = {course["id"]: course for course in courses}
    scheduled_ids = active_course_ids()
    owners = course_plan_owners()
    lectures = [dict(row) for row in db.execute(
        """SELECT l.* FROM lectures l JOIN courses c ON c.id=l.course_id
        WHERE c.owner_user_id=? ORDER BY l.course_id, l.lecture_number""", (user_id,))]
    parts = [dict(row) for row in db.execute(
        """SELECT t.* FROM lecture_tasks t JOIN lectures l ON l.id=t.lecture_id
        JOIN courses c ON c.id=l.course_id WHERE c.owner_user_id=?
        ORDER BY t.lecture_id, t.position""", (user_id,))]
    by_course = defaultdict(list)
    by_lecture = defaultdict(list)
    for lecture in lectures:
        by_course[lecture["course_id"]].append(lecture)
    for part in parts:
        by_lecture[part["lecture_id"]].append(part)
    lecture_by_id = {lecture["id"]: lecture for lecture in lectures}
    part_by_id = {part["id"]: part for part in parts}
    frozen_task_ids = {part["id"] for part in parts
                       if part["id"] in overdue_task_ids
                       and lecture_by_id[part["lecture_id"]]["course_id"]
                       != recalculate_overdue_course_id}
    frozen_lecture_ids = {lecture["id"] for lecture in lectures
                          if lecture["id"] in overdue_lecture_ids
                          and lecture["course_id"] != recalculate_overdue_course_id}
    old_course_deadlines = course_deadlines()
    old_lecture_deadlines = {row["id"]: row["deadline"] for row in lectures}
    old_part_deadlines = {row["id"]: row["deadline"] for row in parts}
    stale_allocations = db.execute("""SELECT a.id, a.lecture_id, a.task_id FROM schedule_allocations a
        JOIN courses owned ON owned.id=a.course_id WHERE owned.owner_user_id=? AND
        ((lecture_id IS NULL AND course_id IN (SELECT id FROM courses WHERE status != 'Hoàn thành'))
         OR (lecture_id IS NOT NULL AND task_id IS NULL AND lecture_id IN
             (SELECT id FROM lectures WHERE status != 'Hoàn thành'))
         OR task_id IN (SELECT id FROM lecture_tasks WHERE status != 'Hoàn thành'))""",
        (user_id,)).fetchall()
    db.executemany("DELETE FROM schedule_allocations WHERE id=?",
                   ((row["id"],) for row in stale_allocations
                    if row["task_id"] not in frozen_task_ids
                    and row["lecture_id"] not in frozen_lecture_ids))
    used = defaultdict(float)
    for row in db.execute("""SELECT a.study_date, SUM(a.hours) AS hours FROM schedule_allocations a
        JOIN courses c ON c.id=a.course_id WHERE a.study_date >= ? AND c.owner_user_id=?
        GROUP BY a.study_date""", (start_key, user_id)):
        used[row["study_date"]] = row["hours"]
    profiles = [dict(row) for row in db.execute(
        "SELECT * FROM user_capacity_profiles WHERE user_id=? ORDER BY effective_from",
        (user_id,))]
    overrides = {row["study_date"]: row["hours"] for row in db.execute(
        "SELECT study_date, hours FROM user_capacity_overrides WHERE user_id=?", (user_id,))}
    work = []
    for course_id in scheduled_ids:
        course = course_by_id[course_id]
        if course["status"] == "Hoàn thành":
            continue
        group = by_course[course["id"]]
        if not group and course["estimated_hours"] > 0:
            work.append((course["id"], None, None, course["estimated_hours"],
                         max(start_key, course["start_date"] or start_key), None))
        for lecture in group:
            if lecture["status"] == "Hoàn thành" or lecture["id"] in frozen_lecture_ids:
                continue
            lecture_parts = by_lecture[lecture["id"]]
            if lecture_parts:
                pending_parts = [part for part in lecture_parts
                                 if part["status"] != "Hoàn thành"
                                 and part["remaining_hours"] > 0
                                 and part["id"] not in frozen_task_ids]
                for index, part in enumerate(pending_parts):
                    barriers = [part["manual_deadline"]]
                    if index == len(pending_parts) - 1:
                        barriers.append(lecture["manual_deadline"])
                    work.append((course["id"], lecture["id"], part["id"],
                                 part["remaining_hours"],
                                 max(start_key, course["start_date"] or start_key),
                                 max((value for value in barriers if value), default=None)))
            else:
                work.append((course["id"], lecture["id"], None, lecture["remaining_hours"],
                             max(start_key, course["start_date"] or start_key),
                             lecture["manual_deadline"]))
    new_deadlines = {}
    queues = defaultdict(list)
    for course_id, lecture_id, task_id, hours, available_from, release_next in work:
        if hours > 0:
            queues[owners[course_id]].append({"course_id": course_id,
                                              "lecture_id": lecture_id,
                                              "task_id": task_id,
                                              "available_from": available_from,
                                              "release_next": release_next,
                                              "remaining": round(hours * 100)})
    positions = {plan_id: 0 for plan_id in queues}
    day = start
    for _ in range(3651):
        pending = [plan_id for plan_id, queue in queues.items()
                   if positions[plan_id] < len(queue)]
        if not pending:
            break
        key = day.isoformat()
        active = [plan_id for plan_id in pending
                  if queues[plan_id][positions[plan_id]]["available_from"] <= key]
        if not active:
            day += timedelta(days=1)
            continue
        available = round((capacity_on(day, profiles, overrides) - used[key]) * 100)
        while available > 0 and active:
            share = max(1, available // len(active))
            consumed_any = False
            for plan_id in active:
                budget = min(share, available)
                while budget > 0 and positions[plan_id] < len(queues[plan_id]):
                    item = queues[plan_id][positions[plan_id]]
                    if item["available_from"] > key:
                        break
                    cents = min(item["remaining"], budget)
                    db.execute("""INSERT INTO schedule_allocations
                        (course_id, lecture_id, task_id, study_date, hours)
                        VALUES (?, ?, ?, ?, ?)""",
                        (item["course_id"], item["lecture_id"], item["task_id"],
                         key, cents / 100))
                    item["remaining"] -= cents
                    used[key] += cents / 100
                    budget -= cents
                    available -= cents
                    consumed_any = True
                    new_deadlines[(item["course_id"], item["lecture_id"], item["task_id"])] = key
                    if item["remaining"] == 0:
                        positions[plan_id] += 1
                        if (item["release_next"]
                                and positions[plan_id] < len(queues[plan_id])):
                            following = queues[plan_id][positions[plan_id]]
                            following["available_from"] = max(
                                following["available_from"], item["release_next"])
                if available <= 0:
                    break
            if not consumed_any:
                break
            active = [plan_id for plan_id, queue in queues.items()
                      if positions[plan_id] < len(queue)
                      and queue[positions[plan_id]]["available_from"] <= key]
        day += timedelta(days=1)
    else:
        raise ValueError("Không thể lập lịch trong 10 năm tới. Hãy tăng số giờ học khả dụng.")

    for course_id, lecture_id, task_id, _hours, _available_from, _release_next in work:
        if task_id is None:
            continue
        if task_id in overdue_task_ids and course_id != recalculate_overdue_course_id:
            continue
        old = old_part_deadlines[task_id]
        automatic = new_deadlines.get((course_id, lecture_id, task_id))
        override = next(part["manual_deadline"] for part in parts if part["id"] == task_id)
        if override and automatic and override < automatic:
            raise ValueError("Hạn phần việc bạn nhập sớm hơn lịch có thể xếp. Hãy tăng giờ học hoặc chọn ngày muộn hơn.")
        new = override or automatic
        if old == new:
            continue
        missed = db.execute("""SELECT * FROM missed_deadlines WHERE task_id=?
            AND new_deadline IS NULL ORDER BY id DESC LIMIT 1""", (task_id,)).fetchone()
        previous = missed["old_deadline"] if missed else old
        db.execute("""INSERT INTO schedule_events
            (course_id, lecture_id, task_id, old_deadline, new_deadline, reason, kind)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (course_id, lecture_id, task_id, previous, new,
             f"Trễ hạn: {missed['reason']}" if missed else reason,
             "trễ hạn" if missed else "điều chỉnh"))
        if missed:
            db.execute("UPDATE missed_deadlines SET new_deadline=? WHERE id=?",
                       (new, missed["id"]))
        db.execute("UPDATE lecture_tasks SET deadline=? WHERE id=?", (new, task_id))

    planned_task_ids = {task_id for _course_id, _lecture_id, task_id, _hours,
                        _available_from, _release_next in work
                        if task_id is not None}
    for part in parts:
        if (part["status"] != "Hoàn thành" and part["id"] not in planned_task_ids
                and part["id"] not in frozen_task_ids
                and part["deadline"] is not None):
            lecture = next(row for row in lectures if row["id"] == part["lecture_id"])
            db.execute("""INSERT INTO schedule_events
                (course_id, lecture_id, task_id, old_deadline, new_deadline, reason, kind)
                VALUES (?, ?, ?, ?, NULL, ?, 'điều chỉnh')""",
                (lecture["course_id"], lecture["id"], part["id"], part["deadline"], reason))
            db.execute("UPDATE lecture_tasks SET deadline=NULL WHERE id=?", (part["id"],))

    for lecture in lectures:
        if lecture["status"] == "Hoàn thành":
            continue
        course_id, lecture_id = lecture["course_id"], lecture["id"]
        if lecture_id in overdue_lecture_ids and course_id != recalculate_overdue_course_id:
            continue
        if by_lecture[lecture_id]:
            dates = [p["manual_deadline"] or
                     (old_part_deadlines[p["id"]] if p["id"] in frozen_task_ids else
                      new_deadlines.get((course_id, lecture_id, p["id"])))
                     for p in by_lecture[lecture_id] if p["status"] != "Hoàn thành"]
            dates = [value for value in dates if value]
            new = max(dates) if dates else None
        else:
            new = new_deadlines.get((course_id, lecture_id, None))
        if lecture["manual_deadline"] and new and lecture["manual_deadline"] < new:
            raise ValueError("Hạn bài học bạn nhập sớm hơn lịch có thể xếp. Hãy tăng giờ học hoặc chọn ngày muộn hơn.")
        new = lecture["manual_deadline"] or new
        old = old_lecture_deadlines[lecture_id]
        if old == new:
            continue
        missed = db.execute("""SELECT * FROM missed_deadlines WHERE lecture_id=?
            AND task_id IS NULL AND new_deadline IS NULL ORDER BY id DESC LIMIT 1""",
            (lecture_id,)).fetchone()
        previous = missed["old_deadline"] if missed else old
        db.execute("""INSERT INTO schedule_events
            (course_id, lecture_id, old_deadline, new_deadline, reason, kind)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (course_id, lecture_id, previous, new,
             f"Trễ hạn: {missed['reason']}" if missed else reason,
             "trễ hạn" if missed else "điều chỉnh"))
        if missed:
            db.execute("UPDATE missed_deadlines SET new_deadline=? WHERE id=?",
                       (new, missed["id"]))
        db.execute("UPDATE lectures SET deadline=? WHERE id=?", (new, lecture_id))

    for course in courses:
        if course["status"] == "Hoàn thành" or by_course[course["id"]]:
            continue
        new = new_deadlines.get((course["id"], None, None))
        old = old_course_deadlines.get(course["id"])
        if old != new:
            db.execute("""INSERT INTO schedule_events
                (course_id, lecture_id, old_deadline, new_deadline, reason, kind)
                VALUES (?, NULL, ?, ?, ?, 'điều chỉnh')""",
                (course["id"], old, new, reason))
    projected = {row["course_id"]: row["deadline"] for row in db.execute("""
        SELECT course_id, MAX(deadline) AS deadline FROM (
            SELECT course_id, study_date AS deadline FROM schedule_allocations
                WHERE study_date>=?
            UNION ALL
            SELECT course_id, manual_deadline AS deadline FROM lectures
                WHERE status!='Hoàn thành' AND manual_deadline IS NOT NULL
            UNION ALL
            SELECT l.course_id, t.manual_deadline AS deadline FROM lecture_tasks t
                JOIN lectures l ON l.id=t.lecture_id
                WHERE t.status!='Hoàn thành' AND t.manual_deadline IS NOT NULL
        ) WHERE course_id IN (SELECT id FROM courses WHERE owner_user_id=?)
        GROUP BY course_id""", (start_key, user_id))}
    for lecture_id in frozen_lecture_ids:
        lecture = lecture_by_id[lecture_id]
        if lecture["deadline"]:
            projected[lecture["course_id"]] = max(
                projected.get(lecture["course_id"]) or "", lecture["deadline"])
    for task_id in frozen_task_ids:
        part = part_by_id[task_id]
        course_id = lecture_by_id[part["lecture_id"]]["course_id"]
        if part["deadline"]:
            projected[course_id] = max(projected.get(course_id) or "", part["deadline"])
    for course in courses:
        group = by_course[course["id"]]
        if group and all(lecture["status"] == "Hoàn thành" for lecture in group):
            finished = max((lecture["completed_at"] for lecture in group
                            if lecture["completed_at"]), default=None)
            db.execute("""UPDATE courses SET status='Hoàn thành', target_date=?
                WHERE id=?""", (finished, course["id"]))
        elif course["status"] != "Hoàn thành":
            db.execute("UPDATE courses SET target_date=? WHERE id=?",
                       (projected.get(course["id"]), course["id"]))
    return new_deadlines


def day_plan(day, plan_ids=None, include_others=False):
    """Allocations for `day`, limited to `plan_ids` when given.

    By default only the signed-in user's own courses are returned. With
    `include_others`, allocations of other users' courses in `plan_ids` are
    included too; the caller must have checked that those plans are visible.
    """
    if include_others and plan_ids is None:
        raise ValueError("include_others requires an explicit list of visible plans")
    user_id = current_user_id()
    query = """SELECT a.*, c.display_name AS course_name, l.lecture_number,
        l.title, l.content, l.deadline, l.status, l.completed_at,
        t.title AS part_title, t.kind AS part_kind, t.content AS part_content,
        t.deadline AS part_deadline, t.status AS part_status,
        t.completed_at AS part_completed_at,
        c.owner_user_id, u.username AS owner_name, (c.owner_user_id != ?) AS is_shared
        FROM schedule_allocations a JOIN courses c ON c.id=a.course_id
        JOIN users u ON u.id=c.owner_user_id
        LEFT JOIN lectures l ON l.id=a.lecture_id
        LEFT JOIN lecture_tasks t ON t.id=a.task_id
        WHERE a.study_date=?"""
    params = [user_id, day.isoformat()]
    if not include_others:
        query += " AND c.owner_user_id=?"
        params.append(user_id)
    if plan_ids is not None:
        if not plan_ids:
            return []
        placeholders = ",".join("?" for _ in plan_ids)
        query += f""" AND EXISTS (SELECT 1 FROM plan_courses pc
            JOIN study_plans p ON p.id=pc.plan_id WHERE pc.course_id=a.course_id
            AND p.status='Đang hoạt động' AND pc.plan_id IN ({placeholders}))"""
        params.extend(plan_ids)
    query += " ORDER BY is_shared, u.username, a.id"
    return get_db().execute(query, params).fetchall()
