# Yêu cầu AI tạo kế hoạch khóa học để nhập vào Lịch Kế Hoạch

Hãy dùng đề cương, danh sách bài học và tài liệu mà tôi cung cấp để chia **mỗi bài chưa hoàn thành** thành các phần việc cụ thể. Các phần việc có thể gồm xem video, đọc tài liệu, làm bài tập, ôn luyện hoặc thực hành. Ước tính số giờ còn cần cho từng phần việc, theo thứ tự nên làm.

Chỉ trả về **một đối tượng JSON hợp lệ**, không có Markdown, lời dẫn hoặc chú thích. JSON phải tuân theo file `plan_format.schema.json` phiên bản 1. Xem `sample_plan.json` để hiểu cấu trúc.

Quy tắc bắt buộc:

1. Dùng đúng tên khóa học và số bài tôi cung cấp để ứng dụng ghép với dữ liệu hiện có. Chỉ đưa bài chưa hoàn thành và chưa có phần việc trong ứng dụng.
2. Với từng bài, tạo ít nhất một phần việc. Mỗi phần việc có `type`, `title`, `hours`; `content` là tùy chọn. `type` phải là một trong `video`, `exercises`, `review`, `reading`, `project`, `other`.
3. `hours` là **số giờ còn cần học**, không phải phút. Dùng số dương, tối đa hai chữ số thập phân, từ 0.01 đến 100 cho mỗi phần việc. Ước lượng cả thời gian làm bài và ôn tập khi phù hợp.
4. Không đưa ngày học hoặc deadline vào JSON. Ứng dụng tự xếp phần việc theo số giờ rảnh từng ngày.
5. Không bịa tiêu đề, nội dung hay thời lượng của một bài nếu tài liệu chưa đủ rõ. Hãy yêu cầu tôi bổ sung đề cương trước khi tạo JSON cho bài đó.
6. Đảm bảo JSON dùng `"schema_version": 1`, có mảng `courses`, mỗi khóa có `name` và mảng `lectures`, mỗi bài có `number`, `title` và mảng `tasks`.

Thông tin tôi cung cấp để lập kế hoạch:

- Tên khóa học chính xác:
- Danh sách số bài chưa hoàn thành:
- Đề cương hoặc link/nội dung bài giảng:
- Mức độ hiểu hiện tại và phần đã học:
- Giới hạn hoặc ưu tiên về thời gian (nếu có):

Sau khi nhận JSON, tôi sẽ mở **Nhập kế hoạch**, xem trước và xác nhận. Ứng dụng sẽ từ chối bài đã hoàn thành hoặc bài đã có phần việc để tránh ghi đè tiến độ.
