# Yêu cầu AI điều chỉnh một khóa học đang thực hiện

Tôi sẽ cung cấp một file JSON snapshot của một khóa học được xuất từ ứng dụng **Lịch Kế Hoạch**. Hãy phân tích tiến độ thực tế, lịch đã xếp, các lần trễ hạn, nhật ký học và những bất cập tôi mô tả để điều chỉnh lại cấu trúc học tập.

Mục tiêu là tạo một kế hoạch khả thi hơn cho phần còn lại của khóa học. Bạn có thể:

- thêm, xóa, tách, gộp hoặc đổi thứ tự bài học;
- thêm, xóa, tách, gộp hoặc đổi thứ tự phần việc;
- sửa tiêu đề, nội dung, ghi chú và loại phần việc;
- điều chỉnh `estimated_hours` và `remaining_hours` theo khối lượng thực tế;
- thêm bài ôn tập hoặc thực hành nếu lịch sử học cho thấy cần thiết;
- giữ nguyên các phần đã hoàn thành, trừ khi tôi yêu cầu sửa rõ ràng.

## Đầu ra bắt buộc

Chỉ trả về **một đối tượng JSON hợp lệ**, không có Markdown, code fence, lời dẫn hoặc giải thích bên ngoài JSON. JSON trả về phải là toàn bộ snapshot phiên bản 2 và tuân theo `course_snapshot_format.schema.json`; không chỉ trả về phần đã thay đổi.

## Quy tắc bảo toàn dữ liệu

1. Giữ nguyên:
   - `schema_version: 2`;
   - `export_type: "course_plan_snapshot"`;
   - `plan.source_plan_id`;
   - `course.source_course_id`.
2. Giữ nguyên toàn bộ `history` và `scheduling_context`. Đây là dữ liệu ngữ cảnh để phân tích, không phải nơi ghi kế hoạch mới.
3. Không tự ý đổi một bài hoặc phần việc đã hoàn thành về chưa hoàn thành. Giữ `status`, `completed_at`, `completed_on_time` và `remaining_hours` của chúng, trừ khi tôi yêu cầu sửa một sai sót cụ thể.
4. Mục có `status: "Hoàn thành"` phải có `completed_at` theo định dạng `YYYY-MM-DD`. Phần việc đã hoàn thành phải có `remaining_hours: 0`.
5. Mục chưa hoàn thành phải có `completed_at: null`. Phần việc chưa hoàn thành phải có `remaining_hours > 0` và không vượt quá `estimated_hours`.
6. Nếu một bài có phần việc:
   - `estimated_hours` của bài bằng tổng `estimated_hours` của các phần việc;
   - `remaining_hours` của bài bằng tổng `remaining_hours` của các phần việc chưa hoàn thành;
   - bài chỉ được đánh dấu `Hoàn thành` khi mọi phần việc đã hoàn thành.
7. `number` của bài phải là số nguyên dương, duy nhất và theo đúng thứ tự học. `position` của phần việc trong mỗi bài phải bắt đầu từ 1, duy nhất và liên tục.
8. `type` của phần việc chỉ được là một trong: `video`, `exercises`, `review`, `reading`, `project`, `other`.
9. `estimated_hours` dùng đơn vị giờ, là số dương cho phần việc và có tối đa hai chữ số thập phân.
10. Với mục chưa hoàn thành, đặt `deadline: null` để ứng dụng tự tính lại. Chỉ dùng `manual_deadline` khi tôi yêu cầu một hạn cố định; nếu không có yêu cầu thì giữ `null`.
11. Có thể bỏ các trường `source_lecture_id` hoặc `source_task_id` ở mục mới. Không tái sử dụng ID của mục cũ cho một nội dung khác.
12. Nếu thông tin tôi cung cấp chưa đủ để điều chỉnh an toàn, hãy hỏi tôi trước. Chỉ xuất JSON sau khi đã đủ dữ liệu.

## Cách ra quyết định

- Ưu tiên xử lý các điểm yếu, bài bị bỏ quên, phần thường trễ hạn và nội dung có điểm hiểu thấp.
- Chia phần việc đủ nhỏ để có thể xếp vào quỹ giờ từng ngày trong snapshot.
- Ước tính cả thời gian xem/đọc, làm bài, kiểm tra đáp án, sửa lỗi và ôn lại.
- Không tăng khối lượng chỉ để làm kế hoạch chi tiết hơn. Mỗi phần việc cần có kết quả đầu ra rõ ràng.
- Dùng nhật ký và lịch sử làm bằng chứng khi thay đổi khối lượng. Không bịa nội dung khóa học nếu snapshot hoặc tài liệu kèm theo không cung cấp đủ thông tin.

## Thông tin tôi cung cấp cho lần điều chỉnh này

- Những bất cập của kế hoạch hiện tại:
- Phần tôi thường hoàn thành nhanh/chậm hơn dự kiến:
- Nội dung tôi đang yếu hoặc đã bỏ quên:
- Mục tiêu và mốc thời gian mới:
- Phần bắt buộc phải giữ hoặc muốn loại bỏ:
- Tài liệu/đề cương bổ sung (nếu có):

Sau khi nhận JSON mới, tôi sẽ nhập bằng chế độ **Ghi đè khóa học trong kế hoạch**, chọn các kế hoạch cần áp dụng, xem trước và xác nhận.
