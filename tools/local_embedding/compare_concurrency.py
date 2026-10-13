"""Explicit benchmark-only GTE concurrency2 server; production service unchanged.

Use the same private config and environment as service.py, with a separate port.
Records timings/counts only, never inputs, vectors, credentials or exception text.
"""
import argparse
import json
import queue
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from service import EmbeddingService, GTEEngine, Handler, RequestError


class SerializedTokenizer:
    def __init__(self, tokenizer, record=None):
        self.tokenizer = tokenizer
        self.lock = threading.Lock()
        self.record = record or (lambda event: None)

    def __call__(self, *args, **kwargs):
        queued = time.perf_counter()
        with self.lock:
            started = time.perf_counter()
            try:
                return self.tokenizer(*args, **kwargs)
            finally:
                self.record({'event': 'tokenize', 'wait_seconds': started - queued,
                             'work_seconds': time.perf_counter() - started})


class ParallelEngine:
    def __init__(self, engine, streams, record):
        self.engine = engine
        self.streams = queue.Queue()
        for stream in streams:
            self.streams.put(stream)
        self.record = record
        self.failed = threading.Event()

    def encode(self, texts):
        if self.failed.is_set():
            raise RequestError('GPU stream failed; restart benchmark', 503)
        stream = self.streams.get(timeout=180)
        started = time.time()
        status = 'error'
        try:
            if self.failed.is_set():
                raise RequestError('GPU stream failed; restart benchmark', 503)
            with self.engine.torch.cuda.stream(stream):
                result = self.engine.encode(texts)
            status = 'ok'
            return result
        finally:
            try:
                stream.synchronize()
            except Exception:
                status = 'stream_error'
                self.failed.set()
                raise
            finally:
                # A poisoned engine rejects further requests before GPU work.
                self.streams.put(stream)
                self.record({'event': 'embedding_request', 'started_epoch': started,
                             'finished_epoch': time.time(), 'status': status,
                             'items': len(texts), 'stream': stream.cuda_stream})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--events', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    events = Path(args.events)
    write_lock = threading.Lock()
    context = threading.local()

    def record(event):
        event = {'request_id': getattr(context, 'request_id', None), **event}
        with write_lock:
            with events.open('a', encoding='utf-8') as output:
                output.write(json.dumps(event, separators=(',', ':')) + '\n')

    engine = GTEEngine(config)
    engine.encode(['서비스 준비 확인'])
    engine.torch.cuda.synchronize()
    engine.tokenizer = SerializedTokenizer(engine.tokenizer, record)

    class TimedGate:
        def __init__(self):
            self.gate = threading.BoundedSemaphore(2)

        def acquire(self, timeout):
            context.request_id = str(time.perf_counter_ns())
            queued = time.perf_counter()
            acquired = self.gate.acquire(timeout=timeout)
            record({'event': 'request_queue', 'epoch': time.time(),
                    'wait_seconds': time.perf_counter() - queued, 'acquired': acquired})
            return acquired

        def release(self):
            self.gate.release()

    def measured_batch(texts):
        t = engine.torch
        try:
            # Same tokenizer/model/normalization and adaptive batch policy as service.py.
            x = engine.tokenizer(texts, padding=True, truncation=False, return_tensors='pt')
            h2d_start = time.perf_counter()
            x = x.to('cuda')
            h2d_seconds = time.perf_counter() - h2d_start
            start, end = t.cuda.Event(enable_timing=True), t.cuda.Event(enable_timing=True)
            enqueue_start = time.perf_counter()
            with t.inference_mode():
                start.record()
                vectors = t.nn.functional.normalize(engine.model(**x).last_hidden_state[:, 0], p=2, dim=1)
                end.record()
                enqueue_seconds = time.perf_counter() - enqueue_start
                copy_start = time.perf_counter()
                vectors = vectors.cpu()
                copy_wait_seconds = time.perf_counter() - copy_start
            end.synchronize()
            gpu_seconds = start.elapsed_time(end) / 1000
            output_start = time.perf_counter()
            if vectors.shape != (len(texts), 768) or not t.isfinite(vectors).all():
                raise RuntimeError('Invalid embedding output')
            result = vectors.tolist()
            record({'event': 'batch', 'epoch': time.time(), 'items': len(texts),
                    'tokens_padded': int(x['input_ids'].numel()), 'gpu_seconds': gpu_seconds,
                    'model_enqueue_wall_seconds': enqueue_seconds, 'h2d_wall_seconds': h2d_seconds,
                    'd2h_and_gpu_wait_wall_seconds': copy_wait_seconds,
                    'output_conversion_seconds': time.perf_counter() - output_start})
            return result
        except engine.torch.cuda.OutOfMemoryError:
            record({'event': 'oom', 'epoch': time.time(), 'items': len(texts)})
            raise

    engine._batch = measured_batch
    streams = [engine.torch.cuda.Stream() for _ in range(2)]
    wrapped = ParallelEngine(engine, streams, record)
    service = EmbeddingService(wrapped, config['api_key'])
    service.lock = TimedGate()
    server = ThreadingHTTPServer((config.get('bind', '127.0.0.1'), config['port']), Handler)
    server.service = service
    record({'event': 'ready', 'epoch': time.time(), 'concurrency': 2,
            'streams': [s.cuda_stream for s in streams]})
    print('GTE comparison ready: one model, two inference slots/streams', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
