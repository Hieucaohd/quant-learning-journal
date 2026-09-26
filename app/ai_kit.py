"""Files a user downloads and uploads to ChatGPT so it can write a course plan JSON."""
import json
from datetime import date
from pathlib import Path

from .db import get_db
from .plan_import import TASK_TYPES


STATIC_DIR = Path(__file__).resolve().parent / "static"
INPUT_HEADING = "Thông tin tôi cung cấp để lập kế hoạch:"
CLOSING_PREFIX = "Sau khi nhận JSON"


def _ranges(numbers):
    """[1, 2, 3, 5, 7, 8] -> '1–3, 5, 7–8'."""
    parts, numbers = [], sorted(numbers)
    start = previous = None
    for number in numbers + [None]:
        if number is not None and previous is not None and number == previous + 1:
            previous = number
            continue
        if start is not None:
            parts.append(str(start) if start == previous else f"{start}–{previous}")
        start = previous = number
    return ", ".join(parts)


def current_courses(user_id):
    """The user's courses and lecture states, as ChatGPT should see them."""
    db = get_db()
    tasked = {row["lecture_id"] for row in db.execute("""SELECT DISTINCT t.lecture_id
        FROM lecture_tasks t JOIN lectures l ON l.id=t.lecture_id
        JOIN courses c ON c.id=l.course_id WHERE c.owner_user_id=?""", (user_id,))}
    courses = []
    for course in db.execute("""SELECT id, display_name, name, description, status
        FROM courses WHERE owner_user_id=? ORDER BY id""", (user_id,)):
        lectures = [{
            "number": row["lecture_number"],
            "title": row["title"],
            "status": row["status"],
            "has_tasks": row["id"] in tasked,
            "needs_tasks": row["status"] != "Hoàn thành" and row["id"] not in tasked,
        } for row in db.execute("""SELECT id, lecture_number, title, status FROM lectures
            WHERE course_id=? ORDER BY lecture_number""", (course["id"],))]
        courses.append({"name": course["display_name"] or course["name"],
                        "description": course["description"],
                        "status": course["status"], "lectures": lectures})
    own_names = {course["name"] for course in courses}
    catalog = [row["name"] for row in db.execute("SELECT name FROM course_catalogs ORDER BY name")
               if row["name"] not in own_names]
    return {"generated_on": date.today().isoformat(), "task_types": TASK_TYPES,
            "my_courses": courses, "other_shared_courses": catalog}


def _context_section(data):
    lines = [f"## Dữ liệu hiện có trong ứng dụng (ngày {data['generated_on']})", "",
             "File `current_courses.json` đính kèm liệt kê đầy đủ. Tóm tắt:", ""]
    for course in data["my_courses"]:
        needs = [l["number"] for l in course["lectures"] if l["needs_tasks"]]
        done = [l["number"] for l in course["lectures"] if l["status"] == "Hoàn thành"]
        tasked = [l["number"] for l in course["lectures"]
                  if l["has_tasks"] and l["status"] != "Hoàn thành"]
        details = [f"cần tạo phần việc: bài {_ranges(needs)}" if needs
                   else "chưa có bài nào cần tạo phần việc"]
        if done:
            details.append(f"đã hoàn thành (bỏ qua): bài {_ranges(done)}")
        if tasked:
            details.append(f"đã có phần việc (bỏ qua): bài {_ranges(tasked)}")
        lines.append(f"- **{course['name']}** ({course['status']}): " + "; ".join(details))
    if not data["my_courses"]:
        lines.append("- Tôi chưa có khóa học nào trong ứng dụng.")
    if data["other_shared_courses"]:
        lines += ["", "Khóa học chung khác đã có trong ứng dụng (nếu lập kế hoạch cho khóa này, "
                  "dùng **đúng** tên): " + ", ".join(data["other_shared_courses"])]
    lines += ["", "Nếu tạo một khóa học hoàn toàn mới: đặt tên rõ ràng, không trùng các tên ở "
              "trên, và đánh số bài từ 1.", ""]
    return lines


def ai_prompt(user_id, data=None):
    """The static prompt, with the user's current courses filled in."""
    base = (STATIC_DIR / "AI_PLAN_PROMPT.md").read_text(encoding="utf-8-sig")
    head, _, rest = base.partition(INPUT_HEADING)
    _, _, closing = rest.partition(CLOSING_PREFIX)
    if not rest or not closing:
        raise RuntimeError("AI_PLAN_PROMPT.md không còn đúng cấu trúc mong đợi")
    lines = _context_section(data or current_courses(user_id)) + [
        INPUT_HEADING, "",
        "- Khóa học và số bài muốn lập kế hoạch (chọn từ danh sách trên hoặc khóa mới):",
        "- Đề cương hoặc link/nội dung bài giảng (tôi đính kèm thêm nếu có):",
        "- Mức độ hiểu hiện tại và phần đã học:",
        "- Giới hạn hoặc ưu tiên về thời gian (nếu có):", "",
        CLOSING_PREFIX + closing.rstrip(), ""]
    return head.rstrip() + "\n\n" + "\n".join(lines)


GUIDE = """# Cách dùng bộ file này với ChatGPT

1. Giải nén file ZIP.
2. Mở ChatGPT và tải lên **4 file**: `PROMPT.md`, `plan_format.schema.json`,
   `sample_plan.json`, `current_courses.json`. Đính kèm thêm đề cương hoặc tài liệu
   của khóa học nếu có.
3. Dán toàn bộ nội dung `PROMPT.md` vào ô chat (hoặc gõ "Hãy làm theo PROMPT.md"),
   điền các dòng còn trống ở cuối prompt rồi gửi.
4. ChatGPT trả về một đối tượng JSON. Lưu thành file `.json` (UTF-8) hoặc sao chép nội dung.
5. Trong Lịch Kế Hoạch, mở **Nhập kế hoạch**, chọn file hoặc dán JSON, bấm
   **Kiểm tra và xem trước**, rồi **Xác nhận nhập**.

Ứng dụng tự kiểm tra JSON và không ghi đè bài đã hoàn thành hoặc bài đã có phần việc.
"""


def ai_kit_files(user_id):
    data = current_courses(user_id)
    return {
        "HUONG_DAN.md": GUIDE,
        "PROMPT.md": ai_prompt(user_id, data),
        "plan_format.schema.json": (STATIC_DIR / "plan_format.schema.json").read_text(
            encoding="utf-8-sig"),
        "sample_plan.json": (STATIC_DIR / "sample_plan.json").read_text(encoding="utf-8-sig"),
        "current_courses.json": json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    }
