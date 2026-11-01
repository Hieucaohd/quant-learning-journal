# Lịch Kế Hoạch

Ứng dụng lập kế hoạch và xếp lịch theo số giờ bạn có mỗi ngày. Mỗi kế hoạch có thể thuộc nhiều loại; hiện hỗ trợ kế hoạch gồm các khóa học. Giao diện web chạy bằng Flask; bản local lưu dữ liệu trong SQLite tại `data/journal.sqlite3`. Bạn có thể xuất Markdown và JSON để tải lên ChatGPT khi cần phân tích tiến độ. Ứng dụng không cần khóa API và bản local có thể chạy không cần kết nối mạng.

## Cài đặt và chạy

Cần Python 3.10 trở lên. Mở terminal tại thư mục `quant-learning-journal`:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py --seed
python run.py
```

Mở <http://127.0.0.1:5000>. Lần chạy đầu tiên, trang thiết lập sẽ yêu cầu tạo tài khoản quản trị viên. Trên macOS/Linux, dùng `source .venv/bin/activate` để kích hoạt môi trường ảo. Bỏ qua `python run.py --seed` nếu muốn bắt đầu với cơ sở dữ liệu trống. Chạy lại lệnh thêm dữ liệu mẫu sẽ không tạo các bản ghi mẫu trùng lặp.

### Chạy trực tiếp trên Windows

Nhấp đúp vào `Quant Learning Journal.exe` trong thư mục dự án. Ứng dụng tự khởi động máy chủ local và mở trình duyệt mặc định. File EXE dùng các thư mục `data/`, `exports/` và `database/` nằm cạnh nó, vì vậy hãy giữ file trong thư mục này để tiếp tục dùng cơ sở dữ liệu hiện tại. Nếu ứng dụng đã chạy, nhấp đúp lần nữa chỉ mở thêm một tab.

Để tạo lại file EXE sau khi sửa mã nguồn:

```powershell
pip install -r requirements-build.txt
.\build_exe.ps1
```

## Triển khai lên Vercel

Ứng dụng hỗ trợ Vercel bằng entrypoint `index.py`. Bản local/EXE tiếp tục dùng SQLite; bản Vercel dùng Turso để dữ liệu tồn tại lâu dài giữa các lần chạy serverless. Xem toàn bộ hướng dẫn tạo database miễn phí, chuyển dữ liệu hiện tại, đặt mật khẩu và deploy tại [DEPLOY_VERCEL.md](DEPLOY_VERCEL.md).

## Nhiều người dùng

- Mỗi người đăng nhập bằng tên người dùng và mật khẩu; mật khẩu được lưu dưới dạng băm.
- Khóa học nằm trong danh mục chung. Khi thêm một khóa học chung, mỗi người nhận vùng tiến độ riêng.
- Lịch học, nhật ký, quỹ giờ, phần nhìn lại và kế hoạch thuộc riêng từng tài khoản.
- Chủ sở hữu có thể chia sẻ kế hoạch cho một tên đăng nhập khác ở chế độ chỉ xem và thu hồi quyền sau đó.
- Quản trị viên tạo tài khoản, đặt lại mật khẩu và xem tiến độ của tất cả người dùng tại mục **Người dùng**.
- Khi nâng cấp, toàn bộ dữ liệu cũ được gán cho tài khoản quản trị viên đầu tiên.

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

## Theo dõi thói quen không cần số giờ

Tại **Kế hoạch → Tạo kế hoạch**, chọn **Theo dõi thói quen** để theo dõi tập thể dục,
đọc sách, thiền hoặc các việc lặp lại. Kế hoạch học tập hiện có tiếp tục hoạt động như cũ.

- Thêm việc, chọn các thứ trong tuần, ngày bắt đầu và ngày kết thúc nếu có. Không cần nhập giờ.
- Mở ngày cần theo dõi ở trang kế hoạch hoặc **Lịch**, chọn **Đã làm**, **Không làm**,
  hoặc **Nghỉ / bỏ qua**, kèm ghi chú tùy chọn. Có thể sửa ngày đã qua hoặc bỏ đánh dấu;
  không thể ghi nhận trước cho ngày tương lai.
- **Chưa đánh dấu** được giữ riêng với **Không làm**. Các ngày không có lịch và buổi chọn nghỉ
  không tính vào tổng cần thực hiện. Thống kê tuần/tháng tính đến hôm nay, không tính buổi tương lai.
- Lịch sử tuần và chuỗi buổi hoàn thành nằm trên trang kế hoạch. Chuỗi bỏ qua ngày không có lịch
  và buổi nghỉ; buổi đã qua chưa đánh dấu hoặc không làm sẽ ngắt chuỗi.
- Để đổi lịch lặp, đặt ngày kết thúc cho việc cũ và tạo việc mới từ ngày áp dụng; lịch sử cũ được giữ.
  Tạm dừng kế hoạch sẽ ẩn các buổi chưa ghi nhận trên lịch chung; trang kế hoạch vẫn cho xem
  và bổ sung lịch sử theo lịch lặp đã cấu hình.
- Có thể chia sẻ kế hoạch ở chế độ chỉ xem. Kết quả được đưa vào JSON và báo cáo Markdown khi xuất dữ liệu.
- Thói quen không sử dụng quỹ giờ học và không làm thay đổi phân bổ hay hạn của khóa học.
- Các việc này được tính trong **Hôm nay cần làm gì?** trên Tổng quan và Lịch, có liên kết mở
  đúng phần việc trong kế hoạch. Kết quả **Đã làm** cũng tính vào chuỗi ngày có tiến độ chung.
- Trong trang kế hoạch, chọn **Tải bộ prompt AI (.zip)** và gửi cả 4 tệp cho AI. Bộ này gồm
  prompt/hướng dẫn, schema, ví dụ và các việc hiện có trong kế hoạch. Có thể tải bộ cho tất cả
  kế hoạch thói quen của mình ở trang **Nhập kế hoạch**.
- Chọn **Nhập phần việc từ AI** (hoặc **Nhập kế hoạch → Thêm việc theo dõi / thói quen**),
  chọn kế hoạch đích, tải/dán JSON rồi xem trước và xác nhận. Có thể tạo kế hoạch mới theo tên
  trong JSON; nếu đã có kế hoạch thói quen cùng tên, việc được thêm vào kế hoạch đó.
  Hỗ trợ việc lặp và việc một lần (ngày bắt đầu = ngày kết thúc, chọn đúng thứ).
- Import chỉ thêm việc, không ghi đè kết quả hay mô tả cũ. Việc trùng tên (không phân biệt
  hoa/thường), các thứ và khoảng ngày được bỏ qua khi nhập lại. **Xuất JSON phần việc** xuất
  cấu trúc kế hoạch để cung cấp ngữ cảnh cho AI; dùng chức năng xuất dữ liệu chung để sao lưu
  cả lịch sử đánh dấu. Xem định dạng trong `app/static/habit_plan.schema.json`.

Schema v4 bổ sung loại kế hoạch và hai bảng `habits`, `habit_entries`. Nâng cấp từ v3 chỉ thêm
cấu trúc mới; xem [hướng dẫn migration](DEPLOY_VERCEL.md#nâng-cấp-schema-database) khi dùng Turso.

Schema v5 thêm `habits.excluded_dates_json` (mặc định `[]`), giữ nguyên các việc và lịch sử cũ.
Prompt AI hỗ trợ `excluded_dates` cho từng phần việc: danh sách tối đa 366 ngày cụ thể
YYYY-MM-DD cần nghỉ lễ/Tết hoặc nghỉ riêng. Các ngày này được liệt kê ở bước xem trước và
trang kế hoạch; không tạo buổi trên lịch, không tính vào tỷ lệ và không ngắt chuỗi buổi của việc.
File cũ không có trường này vẫn nhập được. Import cùng việc nhưng khác danh sách loại trừ
sẽ báo lỗi; muốn đổi lịch, kết thúc việc cũ rồi tạo việc mới từ ngày áp dụng.
AI được yêu cầu hỏi khu vực/năm/phạm vi nghỉ, kiểm tra nguồn chính thức và chờ bạn xác nhận
danh sách trước khi xuất JSON. Ứng dụng không tự tra ngày lễ hay lặp lịch nghỉ sang năm sau.

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

### Xuất và điều chỉnh một khóa học đang thực hiện

Trong trang chi tiết kế hoạch, bấm **Xuất JSON để AI điều chỉnh** ở một khóa học. Snapshot phiên bản 2 chứa thông tin kế hoạch và khóa học, toàn bộ bài học/phần việc, số giờ, trạng thái, ngày hoàn thành, hạn dự kiến, lịch đã xếp, lịch sử đổi hạn/trễ hạn/hoàn thành và các nhật ký liên quan. File cũng có `ai_instructions` để AI biết trường nào được sửa và quy tắc giữ tiến độ.

Tại màn hình nhập có nút **Tải bộ prompt điều chỉnh (.zip)**. Bộ này gồm `PROMPT_DIEU_CHINH_KHOA_HOC.md`, hướng dẫn từng bước và JSON Schema phiên bản 2. Tải snapshot riêng từ trang kế hoạch rồi gửi cả ba file cho AI. Bộ prompt tạo mới phiên bản 1 vẫn có nút tải riêng và giữ nguyên định dạng cũ.

Sau khi AI trả lại JSON, mở **Nhập kế hoạch học**, chọn **Ghi đè khóa học trong kế hoạch**, chọn kế hoạch nguồn và kiểm tra file. Ở bước xem trước, tích những kế hoạch cần nhận bản điều chỉnh:

- Nếu chọn tất cả kế hoạch đang chứa khóa học, hệ thống cập nhật khóa học dùng chung tại chỗ.
- Nếu chỉ chọn một số kế hoạch, hệ thống tạo một bản tiến độ riêng cho các kế hoạch đã chọn; các kế hoạch còn lại tiếp tục dùng dữ liệu cũ.
- Kế hoạch nguồn được chọn sẵn nhưng có thể bỏ chọn. Phải chọn ít nhất một kế hoạch và chỉ kế hoạch đang hoạt động mới có thể nhận bản điều chỉnh.
- Hệ thống sao lưu trước khi ghi đè, giữ nguyên nhật ký, khôi phục các dấu hoàn thành từ `status`, `completed_at` và `remaining_hours`, rồi tự động lập lại lịch và hạn hoàn thành.

Định dạng máy đọc nằm tại `app/static/course_snapshot_format.schema.json`. `source_plan_id` và `source_course_id` phải được giữ nguyên để ứng dụng xác định đúng dữ liệu nguồn. Bài hoặc phần việc có `status` là `Hoàn thành` phải có `completed_at`; phần việc đã hoàn thành phải có `remaining_hours` bằng `0`.

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

### Quy tắc lập lịch khi chỉnh sửa

- Lịch chia số giờ còn lại của một phần việc qua các ngày có giờ học. Hạn tự động là ngày chứa đoạn cuối; tổng giờ các đoạn bằng số giờ còn lại.
- Thêm, chèn, sửa giờ hoặc xóa bài/phần việc đều tính lại các việc còn lại từ hôm nay. Bài mới có 0 giờ cho đến khi thêm phần việc. Hạn kế thừa khi chèn chỉ là hạn tạm thời, không phải hạn cố định do người dùng nhập.
- Mục quá hạn chưa có lý do được giữ tại hạn cũ và hiện trong danh sách cần xử lý. Chúng không ngăn cập nhật lịch của những việc khác. Khi ghi lý do, chỉ mục đó và các việc phụ thuộc được lùi; kế hoạch khác vẫn dùng được quỹ giờ hôm nay.
- Việc đã hoàn thành vẫn hiện trong lịch sử. Phân bổ vào ngày hoàn thành được giữ và chỉ tính quỹ giờ một lần; ngày hoàn thành không tự đổi khi lập lại lịch.
- Khi đổi ngày bắt đầu một khóa về quá khứ, chỉ khóa đó được tính từ ngày đã chọn. Các khóa khác không bị xếp ngược về quá khứ.
- Ô đầu/cuối tháng thuộc tháng liền kề vẫn hiển thị công việc bình thường. Bộ lọc kế hoạch chỉ thay đổi những mục đang xem, không thay đổi cách phân bổ quỹ giờ.
- Nếu công việc đang thực hiện bị thiếu phân bổ, mở lịch sẽ kiểm tra và khôi phục phần thiếu. Có lịch sử cũ không được coi là bằng chứng rằng mọi việc mới đã được lên lịch.
- Khi thao tác không thể tạo lịch hợp lệ, dữ liệu sửa và lịch cùng được hoàn tác. Các thao tác xóa dọn bản ghi liên quan trực tiếp, áp dụng cả SQLite và Turso.

Kiểm thử luồng chỉnh sửa và tính nhất quán nằm trong `tests/test_schedule_integrity.py`. Chạy bằng `python -m unittest discover -s tests`.

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

