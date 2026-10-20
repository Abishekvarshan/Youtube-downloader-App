from __future__ import annotations

import os
import socket
import sys
import threading
import time
import ctypes
from ctypes import wintypes
from pathlib import Path

import uvicorn
import webview


# In a packaged Windows build, keep settings and downloaded files beside the
# executable instead of inside PyInstaller's temporary extraction directory.
ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def load_env_file() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def set_windows_icon(window) -> None:
    """Apply the app icon to the native WebView window and taskbar button."""
    if sys.platform != "win32":
        return
    icon_path = ROOT / "assets" / "streamline.ico"
    if not icon_path.exists():
        return
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.LoadImageW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT)
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.SendMessageW.restype = ctypes.c_ssize_t
    # Use the native WPARAM/LPARAM types: HWND and HICON are pointer-sized on
    # 64-bit Windows, and truncating either can leave the Python host icon in
    # the taskbar even when the shortcut and executable have the right icon.
    icon = user32.LoadImageW(None, str(icon_path), 1, 0, 0, 0x10)  # IMAGE_ICON | LR_LOADFROMFILE
    if icon:
        hwnd = int(window.native.Handle)
        # Set both sizes, including ICON_SMALL2 (used by some Windows taskbar
        # and Alt+Tab paths). The shortcut's IconLocation does not set the
        # icon Windows reads from the running window itself.
        for icon_size in (1, 0, 2):
            user32.SendMessageW(hwnd, 0x0080, icon_size, icon)  # WM_SETICON

        # Some native hosts fall back to the window class icon for the taskbar
        # button even after WM_SETICON. Set those class icons as well.
        set_class_icon = getattr(user32, "SetClassLongPtrW", None)
        if set_class_icon is not None:
            set_class_icon.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p)
            set_class_icon.restype = ctypes.c_void_p
            set_class_icon(hwnd, -14, icon)  # GCLP_HICON
            set_class_icon(hwnd, -34, icon)  # GCLP_HICONSM


def set_windows_app_id() -> None:
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Streamline.YouTubeDownloader")


def main() -> None:
    set_windows_app_id()
    load_env_file()

    data_root = ROOT / "data"
    os.environ["STATE_DIR"] = str(data_root / "state")
    os.environ["DOWNLOAD_DIR"] = str(data_root / "downloads")
    os.environ["CHROME_PROFILE_DIR"] = str(data_root / "chrome-profile")

    port = free_port()
    config = uvicorn.Config("app.main:app", host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="streamline-server", daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"

    for _ in range(200):
        if server.started:
            break
        if not thread.is_alive():
            raise SystemExit("The Streamline server could not start. Check the Python dependencies and .env settings.")
        time.sleep(0.1)
    else:
        server.should_exit = True
        raise SystemExit("The Streamline server did not become ready in time.")

    # WebView disables browser downloads by default. Completed files are served
    # by the local app, so allow the Save video link to open the Windows save
    # flow from the embedded Edge window.
    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window("Streamline YouTube Downloader", url, width=1180, height=820, min_size=(900, 640))
    window.events.shown += lambda: set_windows_icon(window)
    window.events.closed += lambda: setattr(server, "should_exit", True)
    try:
        # pywebview's WinForms host reads this before creating the native
        # window, so Windows gets the correct class and taskbar icon from the
        # first frame instead of briefly inheriting the Python host icon.
        icon_path = ROOT / "assets" / "streamline.ico"
        webview.start(
            gui="edgechromium",
            debug=False,
            icon=str(icon_path) if icon_path.exists() else None,
        )
    finally:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
