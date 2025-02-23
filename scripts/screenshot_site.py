#!/usr/bin/env python
"""Screenshot the static dashboard for the README.

Renders the real page in headless Chromium against the real data payload and
writes PNGs to ``docs/images/``. These are genuine captures of the rendered
output, not mockups.

Requires chromium on PATH. Serves the site over HTTP on an ephemeral port
rather than opening a file:// URL, because Chromium restricts some features
under file:// and the result would differ from the deployed site.
"""
from __future__ import annotations

import http.server
import shutil
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
IMAGES = ROOT / "docs" / "images"

CHROMIUM_CANDIDATES = [
    "chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
    "chrome",
]

#: (filename, width, height, theme) — height is generous because the page is
#: long; chromium clips to the viewport.
SHOTS = [
    ("dashboard-light.png", 1400, 3400, "light"),
    ("dashboard-dark.png", 1400, 1500, "dark"),
    ("dashboard-mobile.png", 390, 2600, "light"),
]


def find_chromium() -> str:
    for name in CHROMIUM_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    raise SystemExit(
        "chromium not found. Install it, or capture the screenshots manually."
    )


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: D102
        pass


def serve(directory: Path) -> tuple[socketserver.TCPServer, int]:
    handler = lambda *a, **kw: QuietHandler(  # noqa: E731
        *a, directory=str(directory), **kw
    )
    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


def capture(binary: str, url: str, target: Path,
            width: int, height: int) -> bool:
    command = [
        binary,
        "--headless",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--hide-scrollbars",
        "--virtual-time-budget=12000",
        f"--window-size={width},{height}",
        f"--screenshot={target}",
        url,
    ]
    result = subprocess.run(command, capture_output=True, timeout=180)
    if target.exists() and target.stat().st_size > 0:
        return True
    sys.stderr.write(result.stderr.decode("utf-8", "ignore")[-400:] + "\n")
    return False


def main() -> int:
    if not (SITE / "index.html").exists():
        raise SystemExit("site/index.html missing. Run 'make site' first.")
    binary = find_chromium()
    IMAGES.mkdir(parents=True, exist_ok=True)
    server, port = serve(SITE)
    base = f"http://127.0.0.1:{port}/"

    # Dark mode needs the preference set before app.js reads localStorage, so
    # it is captured through a bootstrap page rather than a URL parameter.
    bootstrap = SITE / "__dark.html"
    bootstrap.write_text(
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>'
        "<script>try{localStorage.setItem('eu-health-theme','dark')}"
        "catch(e){}</script>"
        '<meta http-equiv="refresh" content="0;url=index.html">'
        "</body></html>",
        encoding="utf-8",
    )

    failures = []
    try:
        for filename, width, height, theme in SHOTS:
            target = IMAGES / filename
            url = base + ("__dark.html" if theme == "dark" else "")
            ok = capture(binary, url, target, width, height)
            size = target.stat().st_size if target.exists() else 0
            if ok:
                print(f"  {filename:24s} {width}x{height}  {size:,} bytes")
            else:
                print(f"  {filename:24s} FAILED")
                failures.append(filename)
            # Reset the preference so the next shot is not affected.
            if theme == "dark":
                bootstrap.unlink(missing_ok=True)
                bootstrap.write_text(
                    '<!DOCTYPE html><html><head><meta charset="utf-8">'
                    "</head><body><script>try{localStorage."
                    "removeItem('eu-health-theme')}catch(e){}</script>"
                    '<meta http-equiv="refresh" content="0;url=index.html">'
                    "</body></html>",
                    encoding="utf-8",
                )
    finally:
        server.shutdown()
        bootstrap.unlink(missing_ok=True)

    if failures:
        raise SystemExit(f"failed: {', '.join(failures)}")
    print(f"\nscreenshots written to {IMAGES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())