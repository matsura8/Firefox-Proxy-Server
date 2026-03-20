from __future__ import annotations

import argparse
import html
import io
import json
import signal
import socketserver
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable, Optional

try:
    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.firefox.options import Options as FirefoxOptions
    from selenium.webdriver.firefox.service import Service as FirefoxService
except ImportError:  # pragma: no cover - exercised indirectly through runtime guard
    webdriver = None
    WebDriverException = Exception
    FirefoxOptions = None
    FirefoxService = None


BOUNDARY = "frame"


@dataclass(slots=True)
class ProxyConfig:
    listen_host: str = "127.0.0.1"
    listen_port: int = 8787
    start_url: str = "https://example.com/"
    addons: list[str] = field(default_factory=list)
    firefox_binary: Optional[str] = None
    geckodriver: Optional[str] = None
    profile_path: Optional[str] = None
    headless: bool = True
    screenshot_interval: float = 0.6
    window_width: int = 1440
    window_height: int = 900


def validate_upstream_url(candidate: str) -> str:
    parsed = urllib.parse.urlparse(candidate.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"URL must be http(s): {candidate!r}")
    return parsed.geturl()


def html_page(config: ProxyConfig, current_url: str, last_error: Optional[str]) -> bytes:
    escaped_url = html.escape(current_url, quote=True)
    escaped_error = html.escape(last_error) if last_error else ""
    error_block = (
        f'<p class="error"><strong>Browser error:</strong> {escaped_error}</p>' if last_error else ""
    )
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Firefox Extension Proxy</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f2efe8;
      --panel: rgba(255, 255, 255, 0.82);
      --ink: #1f252b;
      --accent: #0f766e;
      --accent-strong: #115e59;
      --error: #9f1239;
      --line: rgba(31, 37, 43, 0.14);
      --shadow: 0 18px 50px rgba(15, 23, 42, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: "Segoe UI", "Trebuchet MS", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(15,118,110,0.18), transparent 28rem),
        radial-gradient(circle at bottom right, rgba(249,115,22,0.16), transparent 30rem),
        linear-gradient(135deg, #efe8dc, var(--bg));
      padding: 2rem;
    }}
    .shell {{
      max-width: 1200px;
      margin: 0 auto;
      background: var(--panel);
      backdrop-filter: blur(14px);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }}
    .controls {{
      display: grid;
      gap: 1rem;
      padding: 1.5rem;
      border-bottom: 1px solid var(--line);
    }}
    h1 {{
      margin: 0;
      font-size: 1.5rem;
      letter-spacing: 0.01em;
    }}
    p {{
      margin: 0;
      line-height: 1.45;
    }}
    form {{
      display: flex;
      gap: 0.75rem;
      flex-wrap: wrap;
    }}
    input[type="url"] {{
      flex: 1 1 32rem;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 0.9rem 1rem;
      font: inherit;
      background: rgba(255, 255, 255, 0.9);
    }}
    button {{
      border: 0;
      border-radius: 999px;
      background: var(--accent);
      color: white;
      padding: 0.9rem 1.25rem;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }}
    button:hover {{
      background: var(--accent-strong);
    }}
    .meta {{
      display: flex;
      gap: 0.75rem 1.5rem;
      flex-wrap: wrap;
      font-size: 0.95rem;
    }}
    code {{
      background: rgba(15, 118, 110, 0.08);
      padding: 0.15rem 0.4rem;
      border-radius: 0.4rem;
    }}
    .error {{
      color: var(--error);
    }}
    .viewer {{
      aspect-ratio: 16 / 10;
      background: #d6d3d1;
    }}
    .viewer img {{
      display: block;
      width: 100%;
      height: 100%;
      object-fit: contain;
      background: white;
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="controls">
      <h1>Firefox Extension Proxy</h1>
      <p>Firefox loads the upstream page with its installed add-ons active, then this server exposes the filtered browser session as a local image stream.</p>
      <form method="post" action="/navigate">
        <input name="url" type="url" value="{escaped_url}" placeholder="https://example.com/" required>
        <button type="submit">Open In Firefox</button>
      </form>
      <div class="meta">
        <span>Viewer: <code>/</code></span>
        <span>Snapshot: <code>/snapshot.jpg</code></span>
        <span>MJPEG stream: <code>/stream.mjpg</code></span>
      </div>
      {error_block}
    </section>
    <section class="viewer">
      <img src="/stream.mjpg" alt="Live Firefox stream">
    </section>
  </main>
</body>
</html>
"""
    return body.encode("utf-8")


class FirefoxProxyRuntime:
    def __init__(self, config: ProxyConfig) -> None:
        self.config = config
        self._driver = None
        self._driver_lock = threading.RLock()
        self._current_url = config.start_url
        self._last_error: Optional[str] = None

    @property
    def current_url(self) -> str:
        return self._current_url

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def start(self) -> None:
        if webdriver is None:
            raise RuntimeError(
                "Selenium is not installed. Install it with `python -m pip install selenium`."
            )

        options = FirefoxOptions()
        options.headless = self.config.headless
        options.set_preference("privacy.trackingprotection.enabled", False)
        options.set_preference("browser.cache.disk.enable", False)
        options.set_preference("browser.cache.memory.enable", False)
        if self.config.firefox_binary:
            options.binary_location = self.config.firefox_binary
        if self.config.profile_path:
            options.profile = self.config.profile_path

        service = FirefoxService(executable_path=self.config.geckodriver) if self.config.geckodriver else FirefoxService()
        driver = webdriver.Firefox(service=service, options=options)
        driver.set_window_size(self.config.window_width, self.config.window_height)
        for addon_path in self.config.addons:
            driver.install_addon(str(Path(addon_path).resolve()), temporary=True)
        driver.get(self._current_url)
        with self._driver_lock:
            self._driver = driver
            self._last_error = None

    def stop(self) -> None:
        with self._driver_lock:
            driver = self._driver
            self._driver = None
        if driver is not None:
            driver.quit()

    def navigate(self, url: str) -> None:
        target = validate_upstream_url(url)
        with self._driver_lock:
            if self._driver is None:
                raise RuntimeError("Firefox session is not running.")
            try:
                self._driver.get(target)
                self._current_url = target
                self._last_error = None
            except WebDriverException as exc:
                self._last_error = str(exc)
                raise RuntimeError(f"Firefox navigation failed: {exc}") from exc

    def snapshot_jpeg(self) -> bytes:
        with self._driver_lock:
            if self._driver is None:
                raise RuntimeError("Firefox session is not running.")
            try:
                png_bytes = self._driver.get_screenshot_as_png()
                self._current_url = self._driver.current_url
                self._last_error = None
                return png_to_jpeg(png_bytes)
            except WebDriverException as exc:
                self._last_error = str(exc)
                raise RuntimeError(f"Firefox screenshot failed: {exc}") from exc


def png_to_jpeg(png_bytes: bytes) -> bytes:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - runtime guard
        raise RuntimeError(
            "Pillow is required for JPEG streaming. Install it with `python -m pip install pillow`."
        ) from exc

    with Image.open(io.BytesIO(png_bytes)) as image:
        rgb_image = image.convert("RGB")
        output = io.BytesIO()
        rgb_image.save(output, format="JPEG", quality=80, optimize=True)
        return output.getvalue()


def build_handler(runtime: FirefoxProxyRuntime):
    class FirefoxProxyHandler(BaseHTTPRequestHandler):
        server_version = "FirefoxExtensionProxy/1.0"

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                self._write_html(HTTPStatus.OK, html_page(runtime.config, runtime.current_url, runtime.last_error))
                return
            if parsed.path == "/snapshot.jpg":
                self._write_snapshot()
                return
            if parsed.path == "/stream.mjpg":
                self._write_stream()
                return
            if parsed.path == "/healthz":
                payload = json.dumps(
                    {
                        "ok": True,
                        "current_url": runtime.current_url,
                        "last_error": runtime.last_error,
                    }
                ).encode("utf-8")
                self._write_bytes(HTTPStatus.OK, "application/json; charset=utf-8", payload)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/navigate":
                self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")
                return
            try:
                form = self._read_form()
                url = form.get("url", [""])[0]
                runtime.navigate(url)
            except Exception as exc:
                payload = html_page(runtime.config, runtime.current_url, str(exc))
                self._write_html(HTTPStatus.BAD_REQUEST, payload)
                return
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/")
            self.end_headers()

        def log_message(self, fmt: str, *args: object) -> None:
            message = fmt % args
            sys.stderr.write(f"[http] {self.address_string()} {message}\n")

        def _read_form(self) -> dict[str, list[str]]:
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length).decode("utf-8")
            return urllib.parse.parse_qs(raw_body, keep_blank_values=True)

        def _write_html(self, status: HTTPStatus, payload: bytes) -> None:
            self._write_bytes(status, "text/html; charset=utf-8", payload)

        def _write_bytes(self, status: HTTPStatus, content_type: str, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _write_snapshot(self) -> None:
            try:
                frame = runtime.snapshot_jpeg()
            except Exception as exc:
                message = f"Snapshot error: {exc}".encode("utf-8")
                self._write_bytes(HTTPStatus.SERVICE_UNAVAILABLE, "text/plain; charset=utf-8", message)
                return
            self._write_bytes(HTTPStatus.OK, "image/jpeg", frame)

        def _write_stream(self) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Pragma", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            self.end_headers()

            while True:
                try:
                    frame = runtime.snapshot_jpeg()
                    self.wfile.write(f"--{BOUNDARY}\r\n".encode("ascii"))
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii"))
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                    time.sleep(runtime.config.screenshot_interval)
                except (BrokenPipeError, ConnectionResetError):
                    break
                except Exception as exc:
                    error_payload = f"Stream error: {exc}\n".encode("utf-8")
                    try:
                        self.wfile.write(f"--{BOUNDARY}\r\n".encode("ascii"))
                        self.wfile.write(b"Content-Type: text/plain; charset=utf-8\r\n")
                        self.wfile.write(f"Content-Length: {len(error_payload)}\r\n\r\n".encode("ascii"))
                        self.wfile.write(error_payload)
                        self.wfile.flush()
                    except OSError:
                        pass
                    break

    return FirefoxProxyHandler


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def parse_args(argv: Optional[Iterable[str]] = None) -> ProxyConfig:
    parser = argparse.ArgumentParser(
        description=(
            "Launch Firefox with extensions like uBlock Origin, browse through that filtered session, "
            "and expose the live browser output over local HTTP."
        )
    )
    parser.add_argument("--listen-host", default="127.0.0.1", help="Local host to bind. Default: 127.0.0.1")
    parser.add_argument("--listen-port", default=8787, type=int, help="Local port to bind. Default: 8787")
    parser.add_argument(
        "--start-url",
        default="https://example.com/",
        type=validate_upstream_url,
        help="Initial upstream URL to open inside Firefox.",
    )
    parser.add_argument(
        "--addon",
        dest="addons",
        action="append",
        default=[],
        help="Path to a Firefox add-on XPI to install. Pass multiple times for multiple add-ons.",
    )
    parser.add_argument("--firefox-binary", help="Optional path to firefox.exe")
    parser.add_argument("--geckodriver", help="Optional path to geckodriver.exe")
    parser.add_argument("--profile-path", help="Optional path to a Firefox profile directory")
    parser.add_argument("--no-headless", action="store_true", help="Show the Firefox window instead of headless mode")
    parser.add_argument(
        "--screenshot-interval",
        type=float,
        default=0.6,
        help="Seconds between MJPEG frames. Default: 0.6",
    )
    parser.add_argument("--window-width", type=int, default=1440, help="Firefox viewport width")
    parser.add_argument("--window-height", type=int, default=900, help="Firefox viewport height")
    args = parser.parse_args(list(argv) if argv is not None else None)
    return ProxyConfig(
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        start_url=args.start_url,
        addons=args.addons,
        firefox_binary=args.firefox_binary,
        geckodriver=args.geckodriver,
        profile_path=args.profile_path,
        headless=not args.no_headless,
        screenshot_interval=max(args.screenshot_interval, 0.05),
        window_width=max(args.window_width, 320),
        window_height=max(args.window_height, 240),
    )


def serve(config: ProxyConfig) -> int:
    runtime = FirefoxProxyRuntime(config)
    runtime.start()

    handler = build_handler(runtime)
    server = ReusableThreadingHTTPServer((config.listen_host, config.listen_port), handler)

    stop_event = threading.Event()

    def shutdown(*_: object) -> None:
        stop_event.set()
        server.shutdown()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, shutdown)
        except ValueError:
            pass

    local_url = f"http://{config.listen_host}:{config.listen_port}/"
    print(f"Firefox extension proxy listening on {local_url}")
    print(f"Current upstream URL: {runtime.current_url}")
    if config.addons:
        print("Installed add-ons:")
        for addon in config.addons:
            print(f"  - {Path(addon).resolve()}")
    else:
        print("Installed add-ons: none")

    try:
        server.serve_forever()
    finally:
        server.server_close()
        runtime.stop()
        stop_event.set()
    return 0


def main(argv: Optional[Iterable[str]] = None) -> int:
    config = parse_args(argv)
    try:
        return serve(config)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
