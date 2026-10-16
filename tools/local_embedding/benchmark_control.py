"""Bounded read-only observation of the isolated local benchmark API.

Never retry inside a request or log tokens, payloads or error messages.
Mutation/setup requests deliberately remain outside this client.
"""
import http.client
import json
import socket
import threading
import time


class ControlClient:
    def __init__(self, port, token, *, timeout=2, record=lambda event: None):
        if not 0 < timeout <= 10:
            raise ValueError('CONTROL_TIMEOUT_INVALID')
        self.port, self.token, self.timeout, self.record = port, token, timeout, record

    def call(self, action):
        if action not in {'status', 'resources', 'errors', 'verify'}:
            raise ValueError('READ_ONLY_CONTROL_REQUIRED')
        start = time.perf_counter()
        epoch = time.time()
        deadline = start + self.timeout
        expired = threading.Event()
        transport = []
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=self.timeout)
        phase = 'connect'
        stages = {}
        status = 'error'
        error_type = None
        http_status = None
        response = None

        def expire():
            expired.set()
            if transport:
                try:
                    transport[0].shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        def remaining():
            left = deadline - time.perf_counter()
            if expired.is_set() or left <= 0:
                raise TimeoutError('CONTROL_DEADLINE_EXCEEDED')
            return left

        timer = threading.Timer(self.timeout, expire)
        timer.daemon = True
        timer.start()
        try:
            connection.connect()
            transport.append(connection.sock)
            connection.sock.settimeout(remaining())
            stages['connected_seconds'] = time.perf_counter() - start
            phase = 'request_send'
            connection.request('POST', '/__bench_control', json.dumps({'action': action}),
                               {'Authorization': 'Bearer ' + self.token,
                                'Content-Type': 'application/json', 'Connection': 'close'})
            connection.sock.settimeout(remaining())
            stages['sent_seconds'] = time.perf_counter() - start
            phase = 'response_headers'
            response = connection.getresponse()
            http_status = response.status
            stages['headers_seconds'] = time.perf_counter() - start
            remaining()
            if http_status != 200:
                raise OSError('CONTROL_HTTP_ERROR')
            phase = 'response_body'
            body = response.read(65537)
            remaining()
            if len(body) > 65536:
                raise ValueError('CONTROL_RESPONSE_TOO_LARGE')
            result = json.loads(body)
            phase = 'complete'
            status = 'ok'
            return result
        except Exception as error:
            if expired.is_set() or time.perf_counter() >= deadline:
                error_type = 'TimeoutError'
                raise TimeoutError('CONTROL_DEADLINE_EXCEEDED') from None
            error_type = type(error).__name__
            raise
        finally:
            timer.cancel()
            if response is not None:
                response.close()
            connection.close()
            self.record({'action': action, 'started_epoch': epoch,
                         'elapsed_seconds': time.perf_counter() - start,
                         'phase': phase, 'status': status, 'error_type': error_type,
                         'http_status': http_status, **stages})
