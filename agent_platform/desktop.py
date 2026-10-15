"""Desktop app: the backend runs in-process on a private loopback port and the React UI opens in a
native window (WKWebView on macOS, WebView2 on Windows, WebKitGTK/Qt on Linux). No browser, no hosting."""

from __future__ import annotations

import threading
import traceback

from .config import CONFIG_HOME
from .models import PipelineConfig
from .runs import RunStore
from .server import LocalHTTPServer, make_handler

LOG_FILE = CONFIG_HOME / "desktop.log"
WINDOW_SIZE = (1440, 900)
MIN_WINDOW_SIZE = (960, 600)


class DesktopBridge:
    """Exposed to the page as window.pywebview.api."""

    def __init__(self) -> None:
        self.window = None

    def pick_folder(self) -> str | None:
        import webview

        if self.window is None:
            return None
        result = self.window.create_file_dialog(webview.FileDialog.FOLDER)
        return str(result[0]) if result else None


def run_desktop(config: PipelineConfig) -> None:
    try:
        import webview
    except ImportError as exc:
        raise SystemExit("Desktop mode needs pywebview: pip install -r requirements-desktop.txt") from exc

    httpd = LocalHTTPServer(("127.0.0.1", 0), make_handler(config, RunStore()))
    threading.Thread(target=httpd.serve_forever, name="autocode-backend", daemon=True).start()

    bridge = DesktopBridge()
    bridge.window = webview.create_window(
        "AutoCode",
        f"http://127.0.0.1:{httpd.server_address[1]}/",
        js_api=bridge,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=MIN_WINDOW_SIZE,
    )
    try:
        webview.start()
    except Exception:
        # A windowed app has no terminal; keep the traceback somewhere the user can find it.
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.write_text(traceback.format_exc(), encoding="utf-8")
        raise
    finally:
        httpd.shutdown()
        httpd.server_close()
