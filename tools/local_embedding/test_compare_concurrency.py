import contextlib
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from compare_concurrency import ParallelEngine, SerializedTokenizer, BatchEngine


class Stream:
    def __init__(self, number):
        self.cuda_stream = number
        self.syncs = 0
    def synchronize(self):
        self.syncs += 1


class Tests(unittest.TestCase):
    def test_short_batch_16_preserves_order_and_token_count(self):
        engine = BatchEngine.__new__(BatchEngine)
        engine.short_batch = 16
        engine.tokenizer = lambda texts, **kwargs: {'input_ids': [range(int(t)) for t in texts]}
        calls = []
        def retry(texts):
            calls.append(texts)
            return [[int(t)] for t in texts]
        engine._retry = retry
        texts = [str(i) for i in range(1, 33)]
        self.assertEqual(engine.encode(texts), ([[int(t)] for t in texts], 528))
        self.assertEqual([len(c) for c in calls], [16, 16])

    def test_long_batch_sizes_and_token_limit_unchanged(self):
        engine = BatchEngine.__new__(BatchEngine)
        engine.short_batch = 16
        engine.tokenizer = lambda texts, **kwargs: {'input_ids': [range(int(t)) for t in texts]}
        calls = []
        def retry(texts):
            calls.append(texts)
            return texts
        engine._retry = retry
        texts = ['8192', '2049', '2048', '1025', '1024']
        self.assertEqual(engine.encode(texts), (texts, sum(map(int, texts))))
        self.assertEqual([len(c) for c in calls], [1, 1, 2, 1])
        calls.clear()
        from service import RequestError
        with self.assertRaises(RequestError): engine.encode(['8193'])
        self.assertEqual(calls, [])

    def test_two_streams_bounded_and_output_preserved(self):
        local = threading.local()
        @contextlib.contextmanager
        def context(stream):
            local.stream = stream.cuda_stream
            yield
        lock = threading.Lock()
        active = set()
        peak = []
        def encode(texts):
            with lock:
                self.assertNotIn(local.stream, active)
                active.add(local.stream)
                peak.append(len(active))
            time.sleep(.025)
            with lock: active.remove(local.stream)
            return texts, 1
        engine = SimpleNamespace(encode=encode, torch=SimpleNamespace(cuda=SimpleNamespace(stream=context)))
        streams = [Stream(1), Stream(2)]
        records = []
        wrapped = ParallelEngine(engine, streams, records.append)
        with ThreadPoolExecutor(max_workers=4) as pool:
            values = list(pool.map(lambda i: wrapped.encode([i]), range(6)))
        self.assertEqual(values, [([i], 1) for i in range(6)])
        self.assertEqual(max(peak), 2)
        self.assertEqual(sum(s.syncs for s in streams), 6)
        self.assertEqual(len(records), 6)

    def test_failure_synchronizes_and_releases_slot(self):
        def fail(texts): raise ValueError('synthetic')
        engine = SimpleNamespace(encode=fail, torch=SimpleNamespace(cuda=SimpleNamespace(stream=lambda s: contextlib.nullcontext())))
        stream = Stream(1)
        records = []
        wrapped = ParallelEngine(engine, [stream], records.append)
        with self.assertRaises(ValueError): wrapped.encode(['x'])
        self.assertEqual(stream.syncs, 1)
        engine.encode = lambda texts: (texts, 1)
        self.assertEqual(wrapped.encode(['next']), (['next'], 1))
        self.assertEqual(records[0]['status'], 'error')

    def test_tokenizer_calls_serialized(self):
        state = {'active': 0, 'peak': 0}
        def tokenizer(texts, **kwargs):
            state['active'] += 1
            state['peak'] = max(state['peak'], state['active'])
            time.sleep(.01)
            state['active'] -= 1
            return texts
        wrapped = SerializedTokenizer(tokenizer)
        with ThreadPoolExecutor(max_workers=3) as pool:
            self.assertEqual(list(pool.map(wrapped, range(6))), list(range(6)))
        self.assertEqual(state['peak'], 1)


if __name__ == '__main__': unittest.main()
