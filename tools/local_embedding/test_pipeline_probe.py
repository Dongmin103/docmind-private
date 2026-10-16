import asyncio
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pipeline_probe import PipelineProbe


class ProbeTests(unittest.TestCase):
    def test_queue_wait_separate_from_execution_and_no_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            probe = PipelineProbe(Path(tmp) / 'probe.jsonl')
            probe.start()
            gate = threading.Event()
            with ThreadPoolExecutor(max_workers=1) as pool:
                blocked = pool.submit(gate.wait)
                future = pool.submit(lambda value: value, 'PRIVATE_DOCUMENT')
                time.sleep(.06)
                gate.set()
                blocked.result()
                self.assertEqual(future.result(), 'PRIVATE_DOCUMENT')
            probe.close()
            text = (Path(tmp) / 'probe.jsonl').read_text()
            self.assertNotIn('PRIVATE_DOCUMENT', text)
            events = [json.loads(line) for line in text.splitlines()]
            event = next(e for e in events if e.get('call', '').endswith('<lambda>'))
            self.assertGreater(event['queue_seconds'], .04)
            self.assertLess(event['execution_seconds'], event['queue_seconds'])
            self.assertGreaterEqual(event['cpu_seconds'], 0)

    def test_call_exception_preserved_without_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            probe = PipelineProbe(Path(tmp) / 'probe.jsonl')
            probe.start()
            def fail():
                raise ValueError('PRIVATE_SQL')
            with self.assertRaisesRegex(ValueError, 'PRIVATE_SQL'):
                probe.wrap('db', fail)()
            probe.close()
            text = (Path(tmp) / 'probe.jsonl').read_text()
            self.assertNotIn('PRIVATE_SQL', text)
            event = next(json.loads(x) for x in text.splitlines() if json.loads(x)['event'] == 'call')
            self.assertEqual(event['status'], 'error')

    def test_loop_blockage_captured_without_locals(self):
        with tempfile.TemporaryDirectory() as tmp:
            probe = PipelineProbe(Path(tmp) / 'probe.jsonl', interval=.01, lag_threshold=.025)
            probe.start()
            async def run():
                probe.watch_loop(asyncio.get_running_loop())
                secret = 'PRIVATE_LOCAL'
                time.sleep(.1)
                await asyncio.sleep(.04)
                self.assertEqual(secret, 'PRIVATE_LOCAL')
            asyncio.run(run())
            probe.close()
            text = (Path(tmp) / 'probe.jsonl').read_text()
            self.assertNotIn('PRIVATE_LOCAL', text)
            events = [json.loads(x) for x in text.splitlines()]
            self.assertTrue(any(e['event'] == 'loop_blocked' and e['lag_seconds'] >= .025 for e in events))
            self.assertTrue(any(e['event'] == 'loop_resume' for e in events))

    def test_worker_event_loop_is_monitored_and_removed_after_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            probe = PipelineProbe(Path(tmp) / 'probe.jsonl', interval=.01, lag_threshold=.025)
            probe.start()
            async def work():
                time.sleep(.1)
                await asyncio.sleep(.02)
            thread = threading.Thread(target=lambda: asyncio.run(work()))
            thread.start()
            thread.join()
            time.sleep(.05)
            probe.close()
            events = [json.loads(x) for x in (Path(tmp) / 'probe.jsonl').read_text().splitlines()]
            blocks = [e for e in events if e['event'] == 'loop_blocked']
            self.assertTrue(blocks)
            self.assertTrue(all(e['thread'] == thread.ident for e in blocks))


if __name__ == '__main__':
    unittest.main()
