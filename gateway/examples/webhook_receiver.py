#!/usr/bin/env python3

import os, json, hmac, hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = bytes.fromhex(os.environ.get("BB_WEBHOOK_SECRET", "00"))
_seen = set()

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/brainarbeit-hook":
            return self.send_error(404)
        n = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(n)
        sig = self.headers.get("X-Brainarbeit-Signature", "")
        expect = "sha256=" + hmac.new(SECRET, raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return self.send_error(401, "bad signature")
        delivery = self.headers.get("X-Brainarbeit-Delivery", "")
        if delivery in _seen:
            return self._ok()
        _seen.add(delivery)
        event = json.loads(raw.decode())
        print(f"event id={event.get('id')} kind={event.get('kind')} topic={event.get('topic')}")
        print("  data:", event.get("data"))
        self._ok()

    def _ok(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass

if __name__ == "__main__":
    print("webhook receiver on http://0.0.0.0:9000/brainarbeit-hook")
    ThreadingHTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
