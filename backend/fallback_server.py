"""Lightweight backup server (Python standard library plus PyYAML, no FastAPI needed).

If installing the full requirements fails on venue Wi-Fi, this still runs the whole demo:
    python -m backend.fallback_server
It serves the same dashboard and the same API as the FastAPI app, using the same core code.
For the final deployment, use the FastAPI app (backend.main).
"""
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import api
from .config import ROOT


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, ctype="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            html = (ROOT / "dashboard" / "index.html").read_bytes()
            return self._send(200, html, "text/html; charset=utf-8")
        if self.path == "/api/state":
            return self._send(200, api.get_state())
        if self.path == "/api/health":
            return self._send(200, {"ok": True})
        self._send(404, {"detail": "Not found"})

    def do_POST(self):
        try:
            body = self._body()
            if self.path == "/api/requests":
                return self._send(200, api.submit_request(body.get("text", "")))
            m = re.fullmatch(r"/api/scenarios/(\d+)", self.path)
            if m:
                return self._send(200, api.run_scenario(int(m.group(1))))
            m = re.fullmatch(r"/api/approvals/(\d+)", self.path)
            if m:
                return self._send(200, api.decide(int(m.group(1)), body.get("approve", False)))
            if self.path == "/api/facts/age":
                return self._send(200, api.age_facts(float(body.get("hours", 25))))
            if self.path == "/api/reset":
                return self._send(200, api.reset_all())
            self._send(404, {"detail": "Not found"})
        except ValueError as e:
            self._send(400, {"detail": str(e)})

    def log_message(self, *args):  # keep the terminal quiet
        pass


def main():
    api.reset_all()
    port = int(os.getenv("PORT", "8000"))
    print(f"Fallback server running on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
