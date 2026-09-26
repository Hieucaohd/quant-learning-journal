import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


HOST = "127.0.0.1"
PORTS = range(5000, 5011)


def application_url(port):
    return f"http://{HOST}:{port}/"


def is_running_quant_journal(port):
    try:
        with urllib.request.urlopen(application_url(port), timeout=0.6) as response:
            body = response.read(64_000)
        return response.status == 200 and b"Quant" in body and b"schedule" in body
    except (OSError, urllib.error.URLError):
        return False


def open_when_ready(port):
    url = application_url(port)
    for _ in range(80):
        try:
            with urllib.request.urlopen(url, timeout=0.4) as response:
                if response.status == 200:
                    if os.environ.get("QUANT_JOURNAL_NO_BROWSER") != "1":
                        webbrowser.open(url, new=2)
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)


def write_error(error):
    base = (Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False) else Path(__file__).resolve().parent)
    log_dir = base / "database"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "launcher-error.log").write_text(str(error), encoding="utf-8")


def main():
    for port in PORTS:
        if is_running_quant_journal(port):
            if os.environ.get("QUANT_JOURNAL_NO_BROWSER") != "1":
                webbrowser.open(application_url(port), new=2)
            return

    from werkzeug.serving import make_server
    from app import create_app

    application = create_app()
    last_error = None
    for port in PORTS:
        try:
            server = make_server(HOST, port, application, threaded=True)
            break
        except OSError as error:
            last_error = error
    else:
        raise RuntimeError("Không tìm được cổng trống từ 5000 đến 5010") from last_error

    threading.Thread(target=open_when_ready, args=(port,), daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        write_error(error)
        raise
