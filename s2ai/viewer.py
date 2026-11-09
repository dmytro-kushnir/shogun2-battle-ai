"""Live tactical map in the browser: http://127.0.0.1:8765

The server loop calls `board.update(...)`; the page (viewer.html) polls /data twice a second and draws the
units, the plan's lines and targets, the orders just sent, and the general's intent and reasoning.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = Path(__file__).with_name("viewer.html")


class Board:
    def __init__(self):
        self.lock = threading.Lock()
        self.data: dict = {"battle": None}
        self.log: list[str] = []

    def update(self, **kw) -> None:
        with self.lock:
            self.data.update(kw)

    def note(self, line: str) -> None:
        with self.lock:
            self.log.append(line)
            del self.log[:-40]

    def snapshot(self) -> bytes:
        with self.lock:
            return json.dumps({**self.data, "log": self.log}).encode()


def serve(board: Board, port: int = 8765) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/data"):
                body, ctype = board.snapshot(), "application/json"
            elif self.path in ("/", "/index.html"):
                body, ctype = PAGE.read_bytes(), "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # quiet
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
