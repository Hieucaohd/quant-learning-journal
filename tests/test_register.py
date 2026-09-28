import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.db import get_db


class RegisterTest(unittest.TestCase):
    def make_app(self, **config):
        return create_app({"TESTING": True, "AUTH_DISABLED": False,
                           "DATABASE": str(Path(self.directory.name) / "journal.sqlite3"),
                           "APP_USERNAME": "admin", "APP_PASSWORD": "admin-password", **config})

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = self.make_app()
        self.client = self.app.test_client()

    def tearDown(self):
        self.directory.cleanup()

    def users(self):
        with self.app.app_context():
            return {row["username"]: dict(row) for row in get_db().execute("SELECT * FROM users")}

    def register(self, **fields):
        data = {"username": "lan.nguyen", "display_name": "Lan Nguyễn",
                "password": "mat-khau-8", "confirm_password": "mat-khau-8", **fields}
        return self.client.post("/register", data=data)

    def test_sign_up_creates_a_plain_user_and_logs_in(self):
        login_page = self.client.get("/login").get_data(as_text=True)
        self.assertIn('href="/register"', login_page)
        self.assertEqual(self.client.get("/register").status_code, 200)

        response = self.register(role="admin")  # a forged role field is ignored
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/courses"))
        user = self.users()["lan.nguyen"]
        self.assertEqual((user["role"], user["display_name"]), ("user", "Lan Nguyễn"))
        self.assertEqual(self.client.get("/").status_code, 200)  # signed in
        with self.app.app_context():
            self.assertTrue(get_db().execute(
                "SELECT 1 FROM user_capacity_profiles WHERE user_id=?", (user["id"],)).fetchone())
        # A signed-in user is sent away from the sign-up page.
        self.assertEqual(self.client.get("/register").status_code, 302)

    def test_invalid_input_creates_no_account(self):
        for fields in ({"username": "ab"}, {"username": "tên có dấu"},
                       {"username": "a" * 41}, {"password": "short", "confirm_password": "short"},
                       {"confirm_password": "khac-mat-khau"}, {"display_name": "x" * 81}):
            self.register(**fields)
        self.assertEqual(set(self.users()), {"admin"})

    def test_taken_username_is_rejected_regardless_of_case(self):
        page = self.register(username="ADMIN").get_data(as_text=True)
        self.assertIn("Tên đăng nhập này đã có người dùng", page)
        self.assertEqual(set(self.users()), {"admin"})
        # The form keeps what was typed, except passwords.
        self.assertIn('value="ADMIN"', page)
        self.assertNotIn("mat-khau-8", page)

    def test_registration_can_be_turned_off(self):
        app = self.make_app(ALLOW_REGISTRATION=False)
        client = app.test_client()
        self.assertNotIn('href="/register"', client.get("/login").get_data(as_text=True))
        self.assertEqual(client.get("/register").status_code, 404)
        self.assertEqual(client.post("/register", data={
            "username": "lan.nguyen", "password": "mat-khau-8",
            "confirm_password": "mat-khau-8"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
