import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app import create_app
from app.timezone import local_today, to_local

# 25 hours apart, so their calendar dates always differ.
EAST = "Pacific/Kiritimati"   # UTC+14
WEST = "Pacific/Pago_Pago"    # UTC-11


class TimezoneTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True,
                               "DATABASE": str(Path(self.directory.name) / "journal.sqlite3")})

    def tearDown(self):
        self.directory.cleanup()

    def today_in(self, zone):
        return datetime.now(ZoneInfo(zone)).date()

    def test_today_follows_the_browser_cookie(self):
        for zone in (EAST, WEST):
            with self.app.test_request_context(headers={"Cookie": f"tz={zone}"}):
                self.assertEqual(local_today(), self.today_in(zone))

    def test_invalid_or_missing_zone_falls_back_to_default(self):
        for cookie in ("tz=Not/AZone", "tz=../../etc/passwd", ""):
            with self.app.test_request_context(headers={"Cookie": cookie} if cookie else {}):
                self.assertEqual(local_today(), self.today_in("Asia/Ho_Chi_Minh"))

    def test_schedule_and_footer_use_the_browser_zone(self):
        client = self.app.test_client()
        for zone in (EAST, WEST):
            client.set_cookie("tz", zone)
            page = client.get("/schedule").get_data(as_text=True)
            self.assertIn(f"<h2>Ngày {self.today_in(zone).isoformat()}</h2>", page)
            self.assertIn(f"Múi giờ: {zone}", page)

    def test_utc_timestamps_are_shown_in_local_time(self):
        with self.app.test_request_context(headers={"Cookie": "tz=Asia/Ho_Chi_Minh"}):
            self.assertEqual(to_local("2026-09-27 20:00:00"), "2026-09-28 03:00")
            self.assertEqual(to_local(None), None)
            self.assertEqual(to_local("không phải ngày"), "không phải ngày")


if __name__ == "__main__":
    unittest.main()
