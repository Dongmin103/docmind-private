"""Synthetic signed endpoint for the native Windows incremental smoke test."""

import argparse
import hashlib
import hmac
import json
import secrets
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--scan-delay", type=float, default=0)
    args = parser.parse_args()
    secret = bytes(range(32))
    sequences = {}
    delayed_scans = set()
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            digest = hashlib.sha256(body).hexdigest()
            stamp = self.headers.get("X-DocMind-Timestamp", "")
            nonce = self.headers.get("X-DocMind-Nonce", "")
            expected = hmac.new(secret, f"POST\n{self.path}\n{stamp}\n{nonce}\n{digest}".encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, self.headers.get("X-DocMind-Signature", "")):
                self.send_error(401)
                return
            request = json.loads(body)
            delay = 0
            source = request.get("source_id", "")
            if self.path.endswith("/session"):
                result = {"source_id": source, "epoch": 1, "last_sequence": sequences.get(source, 0),
                          "lease_expires_at": "2099-01-01T00:00:00Z"}
            elif self.path.endswith("/scans/events"):
                result = {"accepted": True, "scan_id": request["scan_id"], "state": "RUNNING"}
                if request["event"] == "batch" and request["scan_id"] not in delayed_scans:
                    delayed_scans.add(request["scan_id"])
                    delay = args.scan_delay
            elif self.path.endswith("/scans/candidates"):
                result = {"candidates": [], "next_cursor": None}
            else:
                assert request["sequence"] == sequences.get(source, 0) + 1
                sequences[source] = request["sequence"]
                result = {"accepted": True, "epoch": 1, "sequence": request["sequence"],
                          "items": [{"observation_id": item["observation_id"],
                                     "state": "DIRTY" if item["kind"] == "dirty" else "INDEX_CURRENT"}
                                    for item in request["items"]]}
            if args.scan_delay or self.path.endswith("/changes"):
                with lock, open(args.log, "a", encoding="utf-8") as log:
                    log.write(json.dumps({**request, "_received_at": time.time(), "_path": self.path}, ensure_ascii=False) + "\n")
            if delay:
                time.sleep(delay)
            output = json.dumps(result).encode()
            response_stamp = str(int(time.time()))
            response_nonce = secrets.token_hex(16)
            response_hash = hashlib.sha256(output).hexdigest()
            signature = hmac.new(secret, f"POST\n{self.path}\n{response_stamp}\n{response_nonce}\n{response_hash}".encode(), hashlib.sha256).hexdigest()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(output)))
            for key, value in {"X-DocMind-Key-Id": "test-key", "X-DocMind-Timestamp": response_stamp,
                               "X-DocMind-Nonce": response_nonce, "X-DocMind-Content-SHA256": response_hash,
                               "X-DocMind-Signature": signature}.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(output)

    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
