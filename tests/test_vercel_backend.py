import gc
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app, turso_credentials_from_env
from app.db import get_db
from scripts.migrate_sqlite_to_turso import main as migrate_to_turso


class VercelBackendTest(unittest.TestCase):
    def test_prefixed_vercel_turso_variables_are_detected(self):
        variables = {
            "quant_learning_journal_TURSO_DATABASE_URL": "libsql://example.turso.io",
            "quant_learning_journal_TURSO_AUTH_TOKEN": "prefixed-token",
        }
        with patch.dict(os.environ, variables, clear=True):
            self.assertEqual(turso_credentials_from_env(),
                             ("libsql://example.turso.io", "prefixed-token"))

    def test_libsql_backend_and_remote_deletion_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "turso-compatible.db"
            app = create_app({
                "TESTING": True,
                "TURSO_DATABASE_URL": str(database),
                "TURSO_AUTH_TOKEN": "test-token",
                "APP_PASSWORD": "",
            })
            client = app.test_client()
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.post("/courses", data={
                "name": "Khóa trên Turso",
                "status": "Chưa bắt đầu",
                "progress": "0",
                "estimated_hours": "0",
            }).status_code, 302)
            with app.app_context():
                row = get_db().execute("SELECT * FROM courses WHERE name=?",
                                       ("Khóa trên Turso",)).fetchone()
                self.assertEqual(dict(row)["name"], "Khóa trên Turso")
                course_id = row["id"]
            self.assertEqual(client.post(f"/courses/{course_id}/delete", data={
                "course_name": "Khóa trên Turso",
            }).status_code, 302)
            with app.app_context():
                backup = get_db().execute(
                    "SELECT label, payload_json FROM deletion_backups ORDER BY id DESC").fetchone()
                self.assertIn(f"course-{course_id}", backup["label"])
                self.assertIn("Khóa trên Turso", backup["payload_json"])
            del client, app
            gc.collect()

    def test_password_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({
                "TESTING": True,
                "DATABASE": str(Path(directory) / "journal.sqlite3"),
                "APP_PASSWORD": "secret",
                "APP_USERNAME": "quant",
                "AUTH_DISABLED": False,
            })
            client = app.test_client()
            self.assertEqual(client.get("/").status_code, 302)
            self.assertEqual(client.post("/login", data={
                "username": "quant", "password": "wrong",
            }).status_code, 200)
            self.assertEqual(client.post("/login", data={
                "username": "quant", "password": "secret",
            }).status_code, 302)
            self.assertEqual(client.get("/").status_code, 200)

    def test_migration_script_copies_local_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite3"
            target = Path(directory) / "target.db"
            local_app = create_app({"TESTING": True, "DATABASE": str(source)})
            with local_app.app_context():
                get_db().execute("INSERT INTO courses (name) VALUES ('Khóa cần chuyển')")
                get_db().commit()
            # Isolate from the developer's .env so the script can never reach a real
            # Turso database, even if credential precedence changes later.
            environment = {name: value for name, value in os.environ.items()
                           if "TURSO_" not in name}
            environment.update({
                "TURSO_DATABASE_URL": str(target),
                "TURSO_AUTH_TOKEN": "test-token",
            })
            with patch.dict(os.environ, environment, clear=True), patch(
                    "app.load_dotenv"), patch(
                    "sys.argv", ["migrate_sqlite_to_turso.py", "--source", str(source)]):
                migrate_to_turso()
            remote_app = create_app({
                "TESTING": True,
                "TURSO_DATABASE_URL": str(target),
                "TURSO_AUTH_TOKEN": "test-token",
            })
            with remote_app.app_context():
                self.assertEqual(get_db().execute(
                    "SELECT name FROM courses").fetchone()["name"], "Khóa cần chuyển")
            del local_app, remote_app
            gc.collect()


if __name__ == "__main__":
    unittest.main()
