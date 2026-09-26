# Triển khai lên Vercel với Turso

## Vì sao không dùng trực tiếp `journal.sqlite3` trên Vercel?

Vercel Function chỉ có filesystem chỉ đọc và thư mục tạm `/tmp`. File trong `/tmp` có thể biến mất khi function được tạo lại, nên không thể dùng file SQLite nằm trong bản deploy làm cơ sở dữ liệu có thể ghi lâu dài.

Cấu hình này giữ hai chế độ:

- Chạy local hoặc file EXE: dùng `data/journal.sqlite3` như hiện tại.
- Chạy trên Vercel: dùng Turso qua `quant_learning_journal_TURSO_DATABASE_URL` và `quant_learning_journal_TURSO_AUTH_TOKEN`.

Turso là SQLite serverless và có gói miễn phí trên Vercel Marketplace.

## 1. Đưa mã nguồn lên Git

Không commit các file bí mật hoặc cơ sở dữ liệu local. `.vercelignore` đã loại `data/`, file EXE, bản sao lưu và môi trường Python khỏi deployment.

Đưa thư mục `quant-learning-journal` lên GitHub, GitLab hoặc Bitbucket rồi import repository đó trong Vercel.

## 2. Tạo Turso database miễn phí

Trong Vercel Dashboard:

1. Mở project vừa tạo.
2. Chọn **Storage** hoặc **Marketplace**.
3. Cài **Turso Cloud** và tạo một database trống.
4. Kết nối database với project.
5. Kiểm tra project đã có đúng hai biến Turso sau:
   - `quant_learning_journal_TURSO_DATABASE_URL`
   - `quant_learning_journal_TURSO_AUTH_TOKEN`

Đây là database production và là **cặp biến Turso duy nhất** ứng dụng đọc. Các biến khác như `TURSO_DATABASE_URL` hay `TURSO_AUTH_TOKEN` (không tiền tố) bị bỏ qua hoàn toàn; nên xóa chúng khỏi Vercel để tránh nhầm lẫn.

Giữ hai giá trị này bí mật. Không ghi chúng vào Git.

## 3. Chuyển dữ liệu SQLite hiện tại lên Turso

Trên máy local, mở PowerShell trong thư mục dự án và cài thư viện:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Nếu đã lưu hai biến Turso trong file `.env` tại thư mục dự án, ứng dụng và script sẽ tự đọc file đó. File `.env` đã nằm trong `.gitignore` và không được đưa lên Git.

Nếu chưa có `.env`, lấy URL và token từ Vercel/Turso Dashboard rồi đặt tạm trong cửa sổ PowerShell hiện tại:

```powershell
$env:quant_learning_journal_TURSO_DATABASE_URL="libsql://..."
$env:quant_learning_journal_TURSO_AUTH_TOKEN="..."
python .\scripts\migrate_sqlite_to_turso.py
```

Script chỉ chấp nhận Turso chưa có khóa học để tránh ghi đè. Nó chuyển khóa học, bài học, phần việc, kế hoạch, nhật ký, lịch và toàn bộ lịch sử từ `data/journal.sqlite3`.

Sau khi chuyển xong, xóa token khỏi phiên terminal nếu muốn:

```powershell
Remove-Item Env:quant_learning_journal_TURSO_DATABASE_URL
Remove-Item Env:quant_learning_journal_TURSO_AUTH_TOKEN
```

## 4. Đặt mật khẩu cho website

Ứng dụng chứa dữ liệu cá nhân và các form có quyền sửa/xóa. Bản Vercel sẽ từ chối khởi động nếu thiếu `APP_PASSWORD`.

Trong **Project Settings → Environment Variables**, thêm:

| Biến | Giá trị |
|---|---|
| `APP_USERNAME` | `quant` hoặc tên bạn muốn |
| `APP_PASSWORD` | mật khẩu ban đầu của quản trị viên |
| `JOURNAL_SECRET_KEY` | chuỗi ngẫu nhiên |

Tạo secret bằng Python:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Dùng kết quả cho `JOURNAL_SECRET_KEY`. Khi mở website, ứng dụng hiển thị trang đăng nhập.

Sau lần deploy đầu tiên, đăng nhập bằng `APP_USERNAME` và `APP_PASSWORD`. Từ mục **Người dùng**, quản trị viên có thể tạo thêm tài khoản, chọn vai trò và đặt lại mật khẩu. Việc đổi `APP_PASSWORD` trên Vercel không tự đổi mật khẩu của tài khoản đã được tạo; hãy dùng chức năng đặt lại mật khẩu trong ứng dụng.

## 5. Deploy

Có thể redeploy từ Vercel Dashboard hoặc dùng CLI:

```powershell
npm install -g vercel
vercel login
vercel link
vercel --prod
```

Vercel tự nhận diện Flask qua entrypoint `index.py` ở thư mục gốc và đọc `.python-version`, `requirements.txt`. Không cần đặt Build Command, Output Directory hoặc rewrite thủ công.

## Nâng cấp schema database

Ứng dụng **không tự chạy migration** trên Turso khi khởi động; nếu schema cũ hơn code, log của Vercel sẽ có cảnh báo. Để nâng cấp, sao lưu database rồi chạy trên máy local (dùng `.env` trỏ tới production):

```powershell
python .\scripts\migrate_database.py        # xem database và phiên bản schema
python .\scripts\migrate_database.py --yes  # chạy migration
```

## 6. Kiểm tra sau deploy

1. Mở URL `https://<project>.vercel.app`.
2. Đăng nhập bằng `APP_USERNAME` và `APP_PASSWORD`.
3. Kiểm tra trang Lịch học và một khóa học.
4. Tạo một nhật ký thử, tải lại trang và xác nhận dữ liệu vẫn còn.
5. Bấm **Xuất dữ liệu học tập** và kiểm tra file ZIP tải xuống.

File Markdown/JSON được tạo trong bộ nhớ hoặc `/tmp` để tải xuống. Nguồn dữ liệu lâu dài vẫn nằm trong Turso.

## Biến môi trường production

```text
quant_learning_journal_TURSO_DATABASE_URL=libsql://...
quant_learning_journal_TURSO_AUTH_TOKEN=...
APP_USERNAME=quant
APP_PASSWORD=...
JOURNAL_SECRET_KEY=...
```

Không thêm các giá trị thật vào `.env` đã commit hoặc bất kỳ file nào được đưa lên Git.
