import sys

from app import create_app
from app.db import seed_demo


app = create_app()

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) == 2 and sys.argv[1] == "--seed":
        with app.app_context():
            seed_demo()
        print("Đã thêm dữ liệu mẫu về khóa học, bài học và nhật ký (không tạo bản trùng).")
    elif len(sys.argv) == 1:
        app.run(debug=True, host="127.0.0.1", port=5000)
    else:
        print("Cách dùng: python run.py [--seed]")
        sys.exit(2)
