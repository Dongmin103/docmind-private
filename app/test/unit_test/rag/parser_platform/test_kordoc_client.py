"""HTTP client carries the per-job PDF safety limit to Kordoc."""

import io
import hashlib
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.error import HTTPError

from rag.parser_platform.kordoc_pilot import KordocPageLimitExceeded, KordocServiceError, parse_pilot_service


class KordocClientTest(unittest.TestCase):
    def test_same_parser_waits_until_previous_response_is_read(self):
        entered = threading.Event()
        second_started = threading.Event()
        second_entered = threading.Event()
        release = threading.Event()
        calls = []

        def respond(request, *, timeout):
            calls.append(request)
            if len(calls) == 1:
                entered.set()
                self.assertTrue(release.wait(3))
            else:
                second_entered.set()
            return io.BytesIO(json.dumps({"source_hash": hashlib.sha256(b"synthetic").hexdigest(),
                                         "source_format": "hwp", "parser_name": "kordoc"}).encode())

        def call(second=False):
            if second:
                second_started.set()
            return parse_pilot_service(b"synthetic", "hwp", service_url="http://serial-parser:8095", timeout=2)

        with patch("rag.parser_platform.kordoc_pilot.urllib.request.urlopen", side_effect=respond):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(call)
                self.assertTrue(entered.wait(2))
                second = pool.submit(call, True)
                self.assertTrue(second_started.wait(2))
                try:
                    self.assertFalse(second_entered.wait(.15), "single-job parser received concurrent requests")
                finally:
                    release.set()
                self.assertEqual(first.result()["source_format"], "hwp")
                self.assertEqual(second.result()["source_format"], "hwp")
        self.assertEqual(len(calls), 2)

    def test_queue_wait_uses_request_deadline_and_does_not_send_expired_job(self):
        entered = threading.Event()
        release = threading.Event()

        def respond(request, *, timeout):
            entered.set()
            self.assertTrue(release.wait(3))
            return io.BytesIO(json.dumps({"source_hash": hashlib.sha256(b"synthetic").hexdigest(),
                                         "source_format": "hwp", "parser_name": "kordoc"}).encode())

        with patch("rag.parser_platform.kordoc_pilot.urllib.request.urlopen", side_effect=respond) as send:
            with ThreadPoolExecutor(max_workers=1) as pool:
                first = pool.submit(parse_pilot_service, b"synthetic", "hwp", service_url="http://deadline-parser:8095", timeout=2)
                self.assertTrue(entered.wait(2))
                try:
                    with self.assertRaises(TimeoutError):
                        parse_pilot_service(b"synthetic", "hwp", service_url="http://deadline-parser:8095", timeout=.05)
                finally:
                    release.set()
                first.result()
        self.assertEqual(send.call_count, 1)

    def test_page_cap_is_sent_and_reported_with_observed_count(self):
        seen = {}

        def fail_with_page_count(request, *, timeout):
            seen.update(json.loads(request.data))
            body = io.BytesIO(json.dumps({
                "code": "PARSER_PAGE_LIMIT_EXCEEDED", "page_count": 31,
            }).encode())
            raise HTTPError(request.full_url, 400, "page cap", {}, body)

        with patch("rag.parser_platform.kordoc_pilot.urllib.request.urlopen", side_effect=fail_with_page_count):
            with self.assertRaises(KordocPageLimitExceeded) as caught:
                parse_pilot_service(
                    b"%PDF-1.7\nsynthetic", "pdf",
                    service_url="http://kordoc-parser:8095", max_pdf_pages=30,
                )
        self.assertEqual(seen["max_pdf_pages"], 30)
        self.assertEqual(caught.exception.page_count, 31)

    def test_invalid_input_code_is_preserved_without_service_details(self):
        def fail(request, *, timeout):
            body = io.BytesIO(b'{"code":"PARSER_INVALID_INPUT","detail":"private"}')
            raise HTTPError(request.full_url, 400, "invalid", {}, body)

        with patch("rag.parser_platform.kordoc_pilot.urllib.request.urlopen", side_effect=fail):
            with self.assertRaises(KordocServiceError) as caught:
                parse_pilot_service(b"synthetic", "pptx", service_url="http://kordoc-parser:8095")
        self.assertEqual(caught.exception.code, "PARSER_INVALID_INPUT")
        self.assertNotIn("private", str(caught.exception))

    def test_pdf_ocr_flag_is_never_sent_to_kordoc(self):
        seen = []

        def fail(request, *, timeout):
            seen.append(json.loads(request.data))
            raise HTTPError(request.full_url, 400, "invalid", {}, io.BytesIO(b'{}'))

        with patch("rag.parser_platform.kordoc_pilot.urllib.request.urlopen", side_effect=fail):
            with self.assertRaises(KordocServiceError):
                parse_pilot_service(b"synthetic", "pdf", service_url="http://kordoc-parser:8095")
        self.assertNotIn("pdf_ocr_requested", seen[0])
        with self.assertRaises(ValueError):
            parse_pilot_service(b"synthetic", "pdf", service_url="http://kordoc-parser:8095",
                                pdf_ocr_requested=True)
