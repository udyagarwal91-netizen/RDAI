"""RDAI.exe entry point: start the app server and open the browser.

The console window is the app - closing it stops RDAI. Data is kept in a 'data' folder
next to RDAI.exe (or %LOCALAPPDATA%\\RDAI if that folder is not writable).
"""
import os
import socket
import sys
import threading
import time
import webbrowser


def base_dir():
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def data_dir():
    exe_dir = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    for d in (os.path.join(exe_dir, "data"),
              os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "RDAI")):
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write_test")
            open(probe, "w").close()
            os.remove(probe)
            return d
        except OSError:
            continue
    raise SystemExit("Cannot find a writable folder for the data file.")


def free_port(preferred=8501):
    for port in [preferred] + list(range(8502, 8600)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise SystemExit("No free port found.")


def open_browser_when_ready(url, port):
    for _ in range(120):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                webbrowser.open(url)
                return
        time.sleep(0.5)


def main():
    db_path = os.path.join(data_dir(), "rdai.db")
    os.environ["RDAI_DB"] = db_path
    sys.path.insert(0, base_dir())

    # Fail here, visibly, if the bundle is incomplete (app.py imports these).
    import rdai.agent, rdai.io_excel, rdai.physical  # noqa: F401,E401
    from rdai.db import connect
    from rdai.seed import load_ledger_csv
    if not os.path.exists(db_path):
        print("First start: creating the database from the production book...")
        tmp = db_path + ".new"
        if os.path.exists(tmp):
            os.remove(tmp)
        conn = connect(tmp)
        load_ledger_csv(conn)
        conn.close()
        os.replace(tmp, db_path)  # only a fully seeded database is ever used

    port = free_port()
    url = f"http://localhost:{port}"
    print("=" * 60)
    print(f"  RDAI is running at {url}")
    print(f"  Data file: {db_path}")
    print("  Keep this window open while using RDAI. Close it to stop.")
    print("=" * 60)
    threading.Thread(target=open_browser_when_ready, args=(url, port), daemon=True).start()

    from streamlit.web import cli as stcli
    sys.argv = ["streamlit", "run", os.path.join(base_dir(), "app.py"),
                "--global.developmentMode=false", "--server.headless=true", f"--server.port={port}",
                "--server.address=localhost", "--browser.gatherUsageStats=false",
                "--server.fileWatcherType=none", "--client.toolbarMode=minimal", "--theme.base=light"]
    sys.exit(stcli.main())


if __name__ == "__main__":
    main()
