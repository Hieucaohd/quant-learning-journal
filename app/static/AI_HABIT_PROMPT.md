# Tạo phần việc theo ngày, không cần số giờ

## Cách sử dụng

1. Giải nén bộ này và gửi cả 4 tệp cho AI. `current_plans.json` chứa ngày hiện tại và các việc trong kế hoạch của bạn; bộ tải từ một kế hoạch chỉ chứa kế hoạch đó.
2. Dán prompt bên dưới rồi bổ sung mục tiêu, khoảng ngày, ngày muốn thực hiện trong tuần và mức độ phù hợp với bạn.
3. Nhận file JSON; vào **Nhập kế hoạch → Thêm việc theo dõi / thói quen**, chọn kế hoạch đích hoặc tạo theo tên trong JSON, xem trước rồi xác nhận. Các việc xuất hiện trên lịch ngay; kế hoạch tạm dừng sẽ hiện lịch khi được kích hoạt lại.
4. Đây là luồng thêm việc: việc cũ và kết quả đánh dấu được giữ nguyên. Nhập lại việc có cùng tên (không phân biệt hoa/thường), ngày bắt đầu/kết thúc và các thứ trong tuần sẽ được bỏ qua. Nếu đổi lịch lặp, hãy kết thúc việc cũ ở trang kế hoạch rồi nhập việc mới từ ngày áp dụng.

## Prompt gửi AI

Hãy giúp tôi xây dựng kế hoạch gồm các phần việc không cần phân bổ số giờ, để đánh dấu đã làm / không làm / nghỉ theo từng ngày. Các việc vẫn phải xuất hiện trên lịch chung của ứng dụng.

Đọc `habit_plan.schema.json`, `sample_plan.json`, `current_plans.json` trước khi tạo. Nếu tôi gửi thêm JSON xuất từ kế hoạch, dùng nó làm ngữ cảnh các việc đã có. Đọc ngày hiện tại từ current_plans.json; không tự đoán ngày. Hỏi lại nếu tôi chưa nêu đủ mục tiêu hoặc lịch thực hiện.

Trả về một file JSON thuần UTF-8 (không bọc Markdown), đúng schema:
- `schema_version`: 1; `plan_type`: "habit".
- `name`, `description`: tên và mô tả kế hoạch.
- `tasks`: từ 1 đến 365 phần việc **mới**, mỗi việc có `title`, `description`, `weekdays`, `start_date`, `end_date`.
- `weekdays`: danh sách số nguyên duy nhất 0–6, tương ứng Thứ 2–Chủ nhật. Một việc hằng ngày dùng [0,1,2,3,4,5,6]; không tách thành một phần việc cho từng ngày.
- Ngày ở dạng YYYY-MM-DD. `end_date` là ngày cuối tính cả ngày đó, hoặc null nếu chưa có ngày kết thúc. Khoảng ngày phải có ít nhất một ngày đúng lịch lặp.
- Việc một lần: đặt start_date bằng end_date và weekdays chứa đúng thứ của ngày đó.
- Không dùng số giờ, giờ bắt đầu, khóa học, bài học, ID, trạng thái hoàn thành hoặc lịch sử. AI chỉ lập kế hoạch; người dùng tự đánh dấu kết quả.
- Không sao chép các việc đã có trong current_plans.json. Khi chọn kế hoạch đích trong ứng dụng, tên/mô tả hiện tại của kế hoạch được giữ. Nếu tạo theo tên JSON mà đã có kế hoạch thói quen cùng tên, ứng dụng thêm việc vào kế hoạch đó.
- Không thay đổi hay xóa việc cũ qua import. Nếu đề xuất đổi lịch lặp, giải thích bước kết thúc việc cũ cho tôi trước khi xuất JSON chứa việc mới.

Mục tiêu và lịch thực hiện tôi muốn: [điền tại đây].
