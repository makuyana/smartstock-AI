"""Pretend Meta Graph server for local tests. Standard library only."""

import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

LOCK = Lock()
CALLS = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/calls"):
            with LOCK:
                body = json.dumps(CALLS).encode()
            self._send(200, body)
            return
        if self.path.startswith("/reset"):
            with LOCK:
                CALLS.clear()
            self._send(200, b'{"ok":true}')
            return
        self._send(200, b'{"status":"ok"}')

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        record = {
            "path": self.path,
            "body": raw.decode("utf-8", "replace"),
            "has_auth": (self.headers.get("Authorization") or "").startswith("Bearer "),
        }
        with LOCK:
            CALLS.append(record)
        message_id = "wamid.MOCK." + uuid.uuid4().hex
        body = json.dumps({"messages": [{"id": message_id}]}).encode()
        self._send(200, body)

    def _send(self, status, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8099), Handler).serve_forever()
