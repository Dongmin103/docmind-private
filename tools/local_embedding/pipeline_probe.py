"""Opt-in, content-free timing for an isolated ingestion benchmark process.

Install once before serving requests and close after all work drains. This is
not imported by the product. Never records arguments, SQL, locals or results.
"""
import asyncio
import contextvars
import functools
import json
from pathlib import Path
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor


def _call_name(fn, args):
    while isinstance(fn, functools.partial):
        args = fn.args + args
        fn = fn.func
    if isinstance(getattr(fn, '__self__', None), contextvars.Context) and args:
        return _call_name(args[0], args[1:])
    return getattr(fn, '__qualname__', type(fn).__name__)


class PipelineProbe:
    def __init__(self, path, *, interval=.1, lag_threshold=.25):
        self.path = Path(path)
        self.interval = interval
        self.lag_threshold = lag_threshold
        self.events = queue.Queue(maxsize=8192)
        self.dropped = 0
        self.stop = threading.Event()
        self.loops = {}
        self.original_submit = None
        self.writer_error = None

    def record(self, event):
        event = {'epoch': time.time(), 'mono_ns': time.perf_counter_ns(), **event}
        try:
            self.events.put_nowait(event)
        except queue.Full:
            self.dropped += 1

    def wrap(self, label, fn):
        @functools.wraps(fn)
        def measured(*args, **kwargs):
            start = time.perf_counter_ns()
            cpu = time.thread_time_ns()
            status = 'error'
            try:
                result = fn(*args, **kwargs)
                status = 'ok'
                return result
            finally:
                self.record({'event': 'call', 'call': label, 'started_ns': start,
                             'execution_seconds': (time.perf_counter_ns() - start) / 1e9,
                             'cpu_seconds': (time.thread_time_ns() - cpu) / 1e9,
                             'thread': threading.get_ident(), 'status': status})
        return measured

    def start(self):
        if self.original_submit is not None:
            raise RuntimeError('Probe already started')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Open synchronously so an invalid destination fails before patching.
        self.output = self.path.open('a', encoding='utf-8', newline='\n')
        self.writer = threading.Thread(target=self._write, daemon=True, name='probe-writer')
        self.writer.start()
        self.original_submit = ThreadPoolExecutor.submit
        original = self.original_submit

        def submit(executor, fn, /, *args, **kwargs):
            queued = time.perf_counter_ns()
            label = _call_name(fn, args)
            def run():
                start = time.perf_counter_ns()
                cpu = time.thread_time_ns()
                status = 'error'
                try:
                    result = fn(*args, **kwargs)
                    status = 'ok'
                    return result
                finally:
                    self.record({'event': 'executor', 'call': label,
                                 'submitted_ns': queued, 'started_ns': start,
                                 'queue_seconds': (start - queued) / 1e9,
                                 'execution_seconds': (time.perf_counter_ns() - start) / 1e9,
                                 'cpu_seconds': (time.thread_time_ns() - cpu) / 1e9,
                                 'thread': threading.get_ident(), 'status': status})
            return original(executor, run)
        self.submit = submit
        ThreadPoolExecutor.submit = submit
        self.original_run = asyncio.run
        original_run = self.original_run

        def monitored_run(main, **kwargs):
            async def watched():
                loop = asyncio.get_running_loop()
                owned = id(loop) not in self.loops
                if owned:
                    self.watch_loop(loop)
                try:
                    return await main
                finally:
                    if owned:
                        state = self.loops.pop(id(loop))
                        state['timer'].cancel()
            return original_run(watched(), **kwargs)
        self.monitored_run = monitored_run
        asyncio.run = monitored_run
        self.sampler = threading.Thread(target=self._sample, daemon=True, name='probe-sampler')
        self.sampler.start()

    def watch_loop(self, loop):
        if id(loop) in self.loops:
            return
        state = {'loop': loop, 'thread': threading.get_ident(), 'last_beat': time.perf_counter()}
        state['timer'] = loop.call_later(self.interval, self._beat, state)
        self.loops[id(loop)] = state

    def _beat(self, state):
        now = time.perf_counter()
        lag = now - state['last_beat'] - self.interval
        if lag >= self.lag_threshold:
            self.record({'event': 'loop_resume', 'lag_seconds': lag, 'thread': state['thread']})
        state['last_beat'] = now
        if not self.stop.is_set():
            state['timer'] = state['loop'].call_later(self.interval, self._beat, state)

    def _sample(self):
        previous = time.perf_counter()
        previous_cpu = time.process_time()
        while not self.stop.wait(self.interval):
            now = time.perf_counter()
            cpu = time.process_time()
            for state in list(self.loops.values()):
                if state['loop'].is_closed():
                    continue
                lag = now - state['last_beat'] - self.interval
                if lag < self.lag_threshold:
                    continue
                frame = sys._current_frames().get(state['thread'])
                stack = []
                while frame is not None and len(stack) < 24:
                    stack.append({'file': Path(frame.f_code.co_filename).name,
                                  'function': frame.f_code.co_name, 'line': frame.f_lineno})
                    frame = frame.f_back
                del frame
                self.record({'event': 'loop_blocked', 'lag_seconds': lag, 'stack': stack,
                             'thread': state['thread'],
                             'sample_wall_seconds': now - previous,
                             'process_cpu_seconds': cpu - previous_cpu})
            previous, previous_cpu = now, cpu

    def _write(self):
        try:
            while not self.stop.is_set() or not self.events.empty():
                try:
                    event = self.events.get(timeout=.1)
                except queue.Empty:
                    continue
                self.output.write(json.dumps(event, separators=(',', ':')) + '\n')
                if self.events.empty():
                    self.output.flush()
        except OSError as error:
            self.writer_error = type(error).__name__

    def close(self):
        if self.original_submit is None:
            return
        if ThreadPoolExecutor.submit is self.submit:
            ThreadPoolExecutor.submit = self.original_submit
        if asyncio.run is self.monitored_run:
            asyncio.run = self.original_run
        for state in list(self.loops.values()):
            state['timer'].cancel()
        self.stop.set()
        self.sampler.join(timeout=2)
        self.writer.join(timeout=2)
        if self.writer.is_alive():
            raise RuntimeError('Probe writer did not drain')
        self.output.close()
        self.original_submit = None
        if self.writer_error or self.dropped:
            raise RuntimeError('Probe events incomplete')
