"""The viewer's time zone, reported by their browser in the `tz` cookie.

The server runs in UTC (on Vercel), so "today" must come from the user's own
zone: otherwise a user in Vietnam would still see yesterday until 07:00.
static/timezone.js sets the cookie from Intl.DateTimeFormat on every page.
"""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app, has_request_context, request


COOKIE_NAME = "tz"
FALLBACK_TIMEZONE = "Asia/Ho_Chi_Minh"
_VALID_NAME = re.compile(r"^[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+){0,2}$")


def _zone(name):
    if not name or len(name) > 64 or not _VALID_NAME.match(name):
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def user_timezone():
    """The browser's zone if it sent a valid one, else the configured default."""
    if has_request_context():
        zone = _zone(request.cookies.get(COOKIE_NAME, ""))
        if zone:
            return zone
    default = current_app.config.get("DEFAULT_TIMEZONE", FALLBACK_TIMEZONE)
    return _zone(default) or ZoneInfo(FALLBACK_TIMEZONE)


def local_now():
    return datetime.now(user_timezone())


def local_today():
    """Today's date where the user is. Use this instead of date.today()."""
    return local_now().date()


def to_local(value, fmt="%Y-%m-%d %H:%M"):
    """Show a UTC 'YYYY-MM-DD HH:MM:SS' timestamp (SQLite CURRENT_TIMESTAMP) locally."""
    if not value:
        return value
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(user_timezone()).strftime(fmt)


def init_app(app):
    app.config.setdefault("DEFAULT_TIMEZONE", FALLBACK_TIMEZONE)
    app.add_template_filter(to_local, "localtime")

    @app.context_processor
    def timezone_context():
        return {"user_timezone": str(user_timezone())}

