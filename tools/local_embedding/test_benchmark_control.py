import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from benchmark_control import ControlClient


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if data['action'] == 'status':
            body = b'[["COMPLETE","COMPLETE","14"]]'
        else:
            body = b'{"error":"PRIVATE_ERROR"}'
        if self.server.mode == 'headers':
            time.sleep(.4)
        try:
            self.send_response(500 if self.server.mode == 'error' else 200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            for byte in body:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
                if self.server.mode == 'drip':
                    time.sleep(.03)
        except (BrokenPipeError, ConnectionResetError):
            pass


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.mode = 'fast'
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.01), daemon=True)
        self.thread.start()
        self.events = []
        self.client = ControlClient(self.server.server_port, 'PRIVATE_TOKEN', timeout=.12, record=self.events.append)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_success_returns_authoritative_status_without_payload_in_log(self):
        self.assertEqual(self.client.call('status'), [['COMPLETE', 'COMPLETE', '14']])
        self.assertEqual(self.events[-1]['status'], 'ok')
        text = json.dumps(self.events)
        self.assertNotIn('PRIVATE_TOKEN', text)
        self.assertNotIn('COMPLETE', text)

    def test_header_stall_expires_and_next_poll_can_succeed(self):
        self.server.mode = 'headers'
        start = time.perf_counter()
        with self.assertRaises(TimeoutError):
            self.client.call('status')
        self.assertLess(time.perf_counter() - start, .32)
        self.assertEqual(self.events[-1]['phase'], 'response_headers')
        self.server.mode = 'fast'
        self.assertEqual(self.client.call('status'), [['COMPLETE', 'COMPLETE', '14']])

    def test_slow_drip_has_total_deadline_not_per_read_timeout(self):
        self.server.mode = 'drip'
        start = time.perf_counter()
        with self.assertRaises(TimeoutError):
            self.client.call('status')
        self.assertLess(time.perf_counter() - start, .32)
        self.assertEqual(self.events[-1]['phase'], 'response_body')

    def test_server_error_is_reported_without_body_or_hidden_retry(self):
        self.server.mode = 'error'
        with self.assertRaises(OSError):
            self.client.call('resources')
        self.assertEqual(len(self.events), 1)
        self.assertNotIn('PRIVATE_ERROR', json.dumps(self.events))
        self.assertEqual(self.events[-1]['http_status'], 500)

    def test_mutating_control_is_not_accepted(self):
        with self.assertRaises(ValueError):
            self.client.call('reset')
        self.assertEqual(self.events, [])


if __name__ == '__main__':
    unittest.main()
