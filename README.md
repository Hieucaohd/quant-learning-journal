# Nhật ký học Quant

Ứng dụng ghi lại quá trình học theo lộ trình Quant Research. Giao diện web chạy bằng Flask; dữ liệu được lưu trong SQLite tại `data/journal.sqlite3`. Bạn có thể xuất Markdown và JSON để tải lên ChatGPT khi cần phân tích tiến độ. Sau khi cài thư viện, ứng dụng không cần tài khoản, khóa API hoặc kết nối mạng.

## Cài đặt và chạy

Cần Python 3.10 trở lên. Mở terminal tại thư mục `quant-learning-journal`:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py --seed
python run.py
```

Mở <http://127.0.0.1:5000>. Trên macOS/Linux, dùng `source .venv/bin/activate` để kích hoạt môi trường ảo. Bỏ qua `python run.py --seed` nếu muốn bắt đầu với cơ sở dữ liệu trống. Chạy lại lệnh thêm dữ liệu mẫu sẽ không tạo các bản ghi mẫu trùng lặp.

### Chạy trực tiếp trên Windows

Nhấp đúp vào `Quant Learning Journal.exe` trong thư mục dự án. Ứng dụng tự khởi động máy chủ local và mở trình duyệt mặc định. File EXE dùng các thư mục `data/`, `exports/` và `database/` nằm cạnh nó, vì vậy hãy giữ file trong thư mục này để tiếp tục dùng cơ sở dữ liệu hiện tại. Nếu ứng dụng đã chạy, nhấp đúp lần nữa chỉ mở thêm một tab.

Để tạo lại file EXE sau khi sửa mã nguồn:

```powershell
pip install -r requirements-build.txt
.\build_exe.ps1
```

## Cách sử dụng

1. Thêm khóa học vào **Danh mục khóa học** hoặc nạp dữ liệu mẫu bằng `--seed`. Khóa mới có thể nằm trong danh mục để học sau; lịch chưa xếp khóa đó.
2. Tại **Kế hoạch học**, tạo một hoặc nhiều kế hoạch và thêm khóa từ danh mục vào từng kế hoạch. Chỉ kế hoạch **Đang hoạt động** được đưa vào lịch. Có thể tạm dừng hoặc xóa kế hoạch mà vẫn giữ khóa học và nhật ký trong danh mục. Một khóa có thể thuộc nhiều kế hoạch.
3. Mở khóa học để thêm bài học, cập nhật trạng thái, mức độ hiểu và ghi chú. Nếu đã có bài học, tiến độ được tính theo số giờ đã hoàn thành; nếu chưa có, bạn có thể nhập tiến độ thủ công.
   Các biểu mẫu ở trang chi tiết khóa học lưu và cập nhật nội dung tại chỗ, giữ nguyên bài/phần việc đang mở và vị trí cuộn. Nếu trình duyệt tắt JavaScript, biểu mẫu vẫn hoạt động bằng cách tải lại trang.
   Ở cuối trang chi tiết có nút **Xóa khóa học**. Trang xác nhận hiển thị số bài học, phần việc, nhật ký và mục lịch sẽ bị xóa. Bạn phải nhập đúng tên khóa học; ứng dụng tạo bản sao lưu SQLite trong `database/backups/` ngay trước khi xóa, rồi tính lại lịch cho các khóa còn lại.
4. Ghi buổi học tại **Nhật ký**. Có thể sửa hoặc xóa các mục đã ghi.
5. Mở **Nhìn lại** để xem số giờ và chủ đề đã học theo tuần hoặc tháng, sau đó ghi điểm mạnh, điểm cần cải thiện và kế hoạch tiếp theo.
6. Nhấn **Xuất dữ liệu học tập**. Trình duyệt tải `quant-learning-export.zip`; ba file tương ứng cũng được lưu trong `exports/`:
   - `learning_summary.md` — tình hình hiện tại và nội dung cần ôn tập
   - `progress_report.md` — tiến độ bài học, các buổi học gần đây và phần nhìn lại
   - `learning_data.json` — toàn bộ dữ liệu khóa học, bài học, nhật ký và phần nhìn lại

## Lịch học và hạn hoàn thành

- Mở **Lịch học** để xem lịch tháng, bấm một ngày để xem khóa học, bài học, nội dung và số giờ cần học trong ngày đó. Bộ lọc cho phép xem một kế hoạch hoặc nhiều kế hoạch cùng lúc. Lịch ghi rõ kế hoạch của từng việc và dự báo ngày kết thúc cho các khóa trong bộ lọc.
- Thời lượng được quản lý theo ba cấp: **tổng giờ khóa học → tổng giờ bài học → tổng giờ phần việc**. Khi một bài có phần việc, tổng giờ bài bằng tổng giờ các phần việc. Khi khóa có bài học, tổng giờ khóa bằng tổng giờ các bài. Giao diện luôn hiển thị cả tổng giờ ban đầu và số giờ còn lại.
- Mở một bài học và bấm **Thêm phần việc** để nhập loại công việc, tiêu đề, nội dung và số giờ dự kiến. Có thể thêm nhiều phần việc lần lượt; hệ thống tự đánh số thứ tự, cộng lại tổng giờ của bài và tính lại lịch cùng hạn hoàn thành. Không thể thêm phần việc mới vào bài đã hoàn thành.
- Trong trang khóa học, biểu mẫu **Thêm hoặc chèn bài học** có thể chèn một bài mới trước bài bất kỳ. Bài mới kế thừa hạn hoàn thành hiện tại của bài tại vị trí đó. Các bài ở vị trí đó và phía sau tự tăng số thứ tự; phần việc, tiến độ, ngày hoàn thành và ghi chú của bài cũ vẫn được giữ, sau đó lịch được tính lại. Việc kế thừa hạn hoàn thành được ghi vào lịch sử.
- Mỗi bài có nút **Xóa bài học** và hộp xác nhận. Ứng dụng sao lưu SQLite trước khi xóa, xóa các phần việc của bài, dồn số thứ tự các bài phía sau, tính lại lịch/hạn hoàn thành và ghi sự kiện xóa ở lịch sử cấp khóa.
- Bộ lập lịch dùng số giờ còn lại ở cấp chi tiết nhất: giờ phần việc nếu bài đã được chia phần việc, giờ bài nếu chưa có phần việc, hoặc tổng giờ khóa nếu chưa có bài. Hệ thống chia khối lượng này vào quỹ giờ của từng ngày và suy ra hạn hoàn thành. Tiến độ khóa học được tính theo tỷ lệ số giờ đã hoàn thành trên tổng giờ.
- **Ngày bắt đầu** của khóa là giới hạn sớm nhất: lịch không xếp công việc của khóa trước ngày này. Ô ngày bắt đầu tự lưu ngay khi chọn và có nút tính lại. Khi đổi ngày, hệ thống lập lại cả lịch theo ngày lẫn hạn dự kiến của **mọi bài và phần việc chưa hoàn thành**, rồi tính **Ngày hoàn thành dự kiến** của khóa. Nếu chọn một ngày trong quá khứ, lịch được tính từ ngày đó và phần việc chưa hoàn thành đã quá hạn cần được xử lý trên trang Lịch học. Các hạn từng sửa tay được bỏ để áp dụng lịch mới; hạn cũ, hạn mới và lý do đổi ngày bắt đầu được giữ trong lịch sử. Ngày hoàn thành thực tế của mục đã xong không thay đổi. Ngày dự kiến của khóa hoàn toàn do lịch sinh ra: giao diện không có trường nhập và backend bỏ qua giá trị do người dùng gửi lên.
- Hạn dự kiến của **mỗi bài và mỗi phần việc** được điền tự động từ lịch. Giao diện chỉ hiển thị hạn hiện tại; bấm **Sửa hạn dự kiến** mới mở trường nhập. Khi lưu, phải ghi lý do và hệ thống lưu hạn cũ, hạn mới, lý do cùng thời điểm thay đổi. Hệ thống chỉ nhận hạn mà số giờ học khả dụng có thể đáp ứng, rồi tính lại ngày kết thúc khóa. Có thể xóa hạn đã sửa để quay về hạn tự động. Nếu nhập ngày **trước ngày bắt đầu khóa và không ở tương lai**, ứng dụng coi đó là ngày đã hoàn thành thực tế, bỏ giờ của mục đó khỏi lịch tương lai và lưu lịch sử hoàn thành. Khi toàn bộ bài đã xong, khóa tự chuyển sang Hoàn thành.
- Các kế hoạch đang hoạt động dùng chung quỹ giờ mỗi ngày. Hệ thống chia đều giờ giữa các kế hoạch còn việc, rồi xếp bài theo thứ tự khóa trong mỗi kế hoạch. Nếu một khóa nằm trong nhiều kế hoạch, nó chỉ chiếm giờ một lần; kế hoạch có độ ưu tiên nhỏ hơn sở hữu phần lịch của khóa đó.
- Mỗi bài học có trường **số giờ còn cần học** và **nội dung cần học**. Khi một bài dài hơn số giờ còn lại trong ngày, lịch chia bài sang ngày tiếp theo. Hạn hoàn thành của bài là ngày kết thúc phần giờ cuối cùng của bài.
- Khóa chưa có danh sách bài dùng **số giờ còn cần học** ở cấp khóa để dự báo. Lịch sẽ ghi chung là “Học nội dung khóa học”; hãy thêm bài để có kế hoạch chi tiết. Nếu cả danh sách bài và ước tính giờ đều trống, ngày kết thúc toàn bộ lộ trình sẽ hiện “Chưa đủ dữ liệu”.
- Lịch mặc định dành 4 giờ mỗi ngày từ thứ Hai đến thứ Sáu, 8 giờ thứ Bảy và Chủ nhật. Bạn có thể đặt lịch giờ học mới từ một ngày trong tương lai hoặc điều chỉnh riêng một ngày. Mỗi thay đổi phải có lý do; hệ thống tính lại các hạn hoàn thành chưa hoàn thành và lưu lịch sử thay đổi.
- Khi đến hạn hoàn thành, đánh dấu bài đã hoàn thành. Nếu chưa xong, điền lý do trễ hạn để lùi hạn hoàn thành và tính lại lịch. Bài quá hạn chưa có lý do sẽ được nhắc ở đầu trang Lịch học. Mỗi lần tính lại, lịch phân bổ cũ của phần việc chưa hoàn thành được thay bằng lịch mới để số giờ không bị lặp; lịch sử đổi hạn vẫn được giữ.
- Trong trang chi tiết khóa học, mỗi bài và phần việc có ô **Ngày hoàn thành**. Bạn có thể ghi nhận việc đã xong vào một ngày trong quá khứ, hoặc sửa ngày đã ghi. Ứng dụng lưu ngày thực tế, tính lại trạng thái đúng hạn/trễ hạn và các hạn hoàn thành còn lại từ hôm nay. Lịch sử ghi nhận và sửa ngày hiển thị ngay trên trang khóa học và nằm trong file JSON xuất ra. Nếu còn mục quá hạn chưa ghi lý do, hãy xử lý các mục đó để hệ thống tính lại toàn bộ hạn hoàn thành.
- Các giờ ở đây là **ước tính công việc còn lại**, không tự trừ theo thời gian ghi trong Nhật ký. Sau khi học một phần bài mà chưa hoàn thành, hãy sửa số giờ còn cần học và ghi lý do để dự báo chính xác hơn.

File JSON xuất ra gồm danh mục khóa học, các kế hoạch và thành viên của chúng, lịch theo ngày, quy tắc giờ học, các lần trễ hạn và lịch sử đổi hạn hoàn thành. File Markdown tóm tắt dự báo và các thay đổi gần đây.

## Nhập kế hoạch phần việc do AI tạo

Mở **Nhập kế hoạch học** để tải file `.json` hoặc dán JSON. Ứng dụng kiểm tra và hiển thị bản xem trước; chỉ khi bạn bấm **Xác nhận nhập và lập lại lịch** dữ liệu mới được ghi vào SQLite. Khóa mới được AI tạo sẽ nằm trong danh mục; thêm khóa đó vào kế hoạch đang hoạt động để hiện trên lịch.

Ba file nằm trong `app/static/` và có thể tải trực tiếp ở màn hình nhập:

- `AI_PLAN_PROMPT.md` — yêu cầu mẫu để gửi AI cùng đề cương và danh sách bài chưa hoàn thành.
- `plan_format.schema.json` — định dạng JSON phiên bản 1.
- `sample_plan.json` — ví dụ một bài gồm xem video, làm bài tập và ôn luyện.

Ví dụ rút gọn:

```json
{
  "schema_version": 1,
  "courses": [{
    "name": "MIT 18.01",
    "lectures": [{
      "number": 13,
      "title": "Bài 13",
      "tasks": [
        {"type": "video", "title": "Xem bài giảng", "hours": 1.5},
        {"type": "exercises", "title": "Làm bài tập", "hours": 1.25},
        {"type": "review", "title": "Ôn luyện", "hours": 0.75}
      ]
    }]
  }]
}
```

Tên khóa học và số bài phải khớp với dữ liệu hiện có để bổ sung đúng bài. Khóa/bài chưa có sẽ được tạo. Ứng dụng từ chối bài đã hoàn thành hoặc đã có phần việc để bảo vệ tiến độ; không ghi đè tiêu đề hoặc nội dung bạn đã tự viết. Tổng giờ còn cần học của bài bằng tổng giờ các phần việc. Mỗi phần việc có hạn riêng, nút hoàn thành và biểu mẫu ghi lý do nếu trễ. Trong từng kế hoạch, lịch xếp theo thứ tự khóa học, bài học, rồi phần việc. Bản xuất JSON chứa cả phần việc và lịch sử nhập.

Trang Tổng quan dùng 700 giờ tổng và 36 giờ mỗi tuần làm mốc tham chiếu theo kế hoạch học. Đây không phải điều kiện hoàn thành. Số ngày học liên tiếp được tính từ hôm nay hoặc hôm qua, dựa trên những ngày có ít nhất một mục nhật ký.

## Dữ liệu và sao lưu

File `data/journal.sqlite3` là nơi lưu dữ liệu chính. Để sao lưu, hãy dừng ứng dụng rồi sao chép file này. Các file xuất là bản chụp dữ liệu để chia sẻ, chưa có chức năng nhập lại để khôi phục. Ứng dụng chỉ lắng nghe tại `127.0.0.1` và dành cho sử dụng cá nhân trên máy. Lệnh `python run.py` bật chế độ gỡ lỗi của Flask khi phát triển; không đưa máy chủ này ra mạng công cộng.

Khi mở phiên bản mới trên cơ sở dữ liệu cũ, ứng dụng tự thêm các bảng và trường cần thiết. Lần đầu thêm tính năng nhiều kế hoạch, mọi khóa hiện có được đưa vào kế hoạch mặc định **Lộ trình Quant** để giữ lịch cũ. Các khóa học mẫu chưa có bài được gán số giờ ước tính ban đầu: MIT 18.02 (120), MIT 18.06 (100), MIT 6.041 (140), Finance MicroMasters (220). Bạn có thể sửa các ước tính này trong từng khóa. Nội dung do bạn tự nhập được giữ nguyên.

## Cấu trúc dự án

```text
quant-learning-journal/
├── app/                 Route Flask, lập lịch, truy cập dữ liệu, báo cáo, giao diện, CSS
├── data/                Cơ sở dữ liệu SQLite trên máy
├── exports/             Các file Markdown và JSON xuất gần nhất
├── database/            Dành cho việc chuyển đổi dữ liệu và bản sao lưu sau này
├── requirements.txt
└── run.py
```

Các thư mục `data/`, `exports/` và `database/` có file giữ chỗ để hiện trong mã nguồn. Cơ sở dữ liệu và file xuất được loại khỏi Git.

