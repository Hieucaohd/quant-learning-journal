# Tạo phần việc theo ngày, không cần số giờ

## Cách sử dụng

1. Giải nén bộ này và gửi cả 4 tệp cho AI. `current_plans.json` chứa ngày hiện tại và các việc trong kế hoạch của bạn; bộ tải từ một kế hoạch chỉ chứa kế hoạch đó.
2. Dán prompt bên dưới rồi bổ sung mục tiêu, khoảng ngày, ngày muốn thực hiện trong tuần và mức độ phù hợp với bạn. Nếu muốn nghỉ lễ/Tết, nêu quốc gia/khu vực, năm áp dụng và có nghỉ bù hay không; có thể gửi lịch nghỉ của riêng bạn.
3. Nhận file JSON; vào **Nhập kế hoạch → Thêm việc theo dõi / thói quen**, chọn kế hoạch đích hoặc tạo theo tên trong JSON, xem trước rồi xác nhận. Các việc xuất hiện trên lịch ngay; kế hoạch tạm dừng sẽ hiện lịch khi được kích hoạt lại.
4. Đây là luồng thêm việc: việc cũ và kết quả đánh dấu được giữ nguyên. Nhập lại việc có cùng tên (không phân biệt hoa/thường), ngày bắt đầu/kết thúc và các thứ trong tuần sẽ được bỏ qua. Nếu đổi lịch lặp, hãy kết thúc việc cũ ở trang kế hoạch rồi nhập việc mới từ ngày áp dụng.

## Prompt gửi AI

Hãy giúp tôi xây dựng kế hoạch gồm các phần việc không cần phân bổ số giờ, để đánh dấu đã làm / không làm / nghỉ theo từng ngày. Các việc vẫn phải xuất hiện trên lịch chung của ứng dụng.

Đọc `habit_plan.schema.json`, `sample_plan.json`, `current_plans.json` trước khi tạo. Nếu tôi gửi thêm JSON xuất từ kế hoạch, dùng nó làm ngữ cảnh các việc đã có. Đọc ngày hiện tại từ current_plans.json; không tự đoán ngày. Hỏi lại nếu tôi chưa nêu đủ mục tiêu hoặc lịch thực hiện.

Trả về một file JSON thuần UTF-8 (không bọc Markdown), đúng schema:
- `schema_version`: 1; `plan_type`: "habit".
- `name`, `description`: tên và mô tả kế hoạch.
- `tasks`: từ 1 đến 365 phần việc **mới**, mỗi việc có `title`, `description`, `weekdays`, `start_date`, `end_date`, `excluded_dates`.
- `weekdays`: danh sách số nguyên duy nhất 0–6, tương ứng Thứ 2–Chủ nhật. Một việc hằng ngày dùng [0,1,2,3,4,5,6]; không tách thành một phần việc cho từng ngày.
- Ngày ở dạng YYYY-MM-DD. `end_date` là ngày cuối tính cả ngày đó, hoặc null nếu chưa có ngày kết thúc. Khoảng ngày phải có ít nhất một ngày đúng lịch lặp.
- Việc một lần: đặt start_date bằng end_date và weekdays chứa đúng thứ của ngày đó.
- `excluded_dates`: các ngày nghỉ lễ/Tết hoặc nghỉ riêng cần bỏ khỏi lịch của **từng phần việc**, dạng danh sách ngày YYYY-MM-DD không trùng, tối đa 366 ngày. Ví dụ cú pháp: `"excluded_dates": ["2027-01-01"]`. Dùng `[]` khi không loại ngày nào; file cũ thiếu trường này vẫn được hiểu là `[]`.
- Hỏi tôi muốn áp dụng lịch nghỉ của quốc gia/khu vực nào, năm nào, có gồm ngày nghỉ bù và nghỉ riêng không. Không mặc định lịch nghỉ từ ngôn ngữ tôi dùng. Chỉ áp dụng những ngày nghỉ tôi yêu cầu.
- Khi tôi yêu cầu tự tìm ngày lễ, tra nguồn chính thức cho đúng năm và khu vực, đặc biệt với Tết âm lịch và lịch nghỉ bù. Trình bày danh sách ngày cùng nguồn để tôi xác nhận **trước khi xuất JSON**. Nếu không thể tra cứu hoặc chưa có lịch chính thức, hỏi tôi cung cấp lịch; không tự đoán.
- Nếu kế hoạch không có ngày kết thúc, hỏi phạm vi năm cần loại ngày lễ. Danh sách ngày loại trừ là ngày cụ thể, không tự lặp sang năm sau. Chép danh sách phù hợp vào từng phần việc; không tạo việc riêng hay trạng thái “nghỉ” cho các ngày này.
- Ngày bị loại không hiện thành việc cần làm, không tính vào tỷ lệ hoàn thành và không ngắt chuỗi buổi của phần việc. Đảm bảo việc có ngày kết thúc vẫn còn ít nhất một buổi thực hiện sau khi loại ngày nghỉ. Bản xem trước phải cho tôi kiểm tra các ngày đã loại.
- Không dùng số giờ, giờ bắt đầu, khóa học, bài học, ID, trạng thái hoàn thành hoặc lịch sử. AI chỉ lập kế hoạch; người dùng tự đánh dấu kết quả.
- Không sao chép các việc đã có trong current_plans.json. Khi chọn kế hoạch đích trong ứng dụng, tên/mô tả hiện tại của kế hoạch được giữ. Nếu tạo theo tên JSON mà đã có kế hoạch thói quen cùng tên, ứng dụng thêm việc vào kế hoạch đó.
- Không thay đổi hay xóa việc cũ qua import. Nếu đề xuất đổi lịch lặp, giải thích bước kết thúc việc cũ cho tôi trước khi xuất JSON chứa việc mới.
- Với việc đã có cùng tên và lịch lặp, chỉ được nhập lại nếu giữ nguyên `excluded_dates`; danh sách khác sẽ bị từ chối để tránh tạo trùng hay thay đổi lịch sử. Muốn áp dụng lịch nghỉ mới, hướng dẫn kết thúc việc cũ rồi tạo việc mới từ ngày áp dụng.

Mục tiêu và lịch thực hiện tôi muốn: [điền tại đây].

Lịch nghỉ muốn áp dụng (quốc gia/khu vực, năm, nghỉ bù, nghỉ riêng): [điền tại đây hoặc “không loại ngày nào”].
