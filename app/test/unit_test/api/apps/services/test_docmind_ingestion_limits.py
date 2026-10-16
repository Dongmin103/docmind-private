import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import threading

import pytest


def load():
    path = Path(__file__).resolve().parents[5] / 'api/apps/services/docmind_ingestion_limits.py'
    assert path.exists(), 'Cross-loop ingestion limiter is missing'
    spec = importlib.util.spec_from_file_location('ingestion_limits', path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_two_event_loops_share_capacity_two():
    limiter = load().IngestionRequestLimiter(2)
    state = {'active': 0, 'peak': 0, 'finished': 0}
    lock = threading.Lock()
    barrier = threading.Barrier(2)
    async def request():
        async with limiter:
            with lock:
                state['active'] += 1
                state['peak'] = max(state['active'], state['peak'])
            await asyncio.sleep(.03)
            with lock:
                state['active'] -= 1
                state['finished'] += 1
    async def document():
        await asyncio.gather(*(request() for _ in range(4)))
    def worker():
        barrier.wait(timeout=3)
        asyncio.run(document())
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        for future in futures: future.result(timeout=5)
    assert state == {'active': 0, 'peak': 2, 'finished': 8}


def test_cancel_waiter_does_not_leak_permit():
    limiter = load().IngestionRequestLimiter(1)
    async def run():
        await limiter.acquire()
        waiter = asyncio.create_task(limiter.acquire())
        await asyncio.sleep(.02)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError): await waiter
        limiter.release()
        await asyncio.wait_for(limiter.acquire(), .5)
        limiter.release()
    asyncio.run(run())


def test_exception_releases_permit_and_repeated_closed_loops():
    limiter = load().IngestionRequestLimiter(1)
    async def run():
        with pytest.raises(ValueError):
            async with limiter: raise ValueError('body failed')
        async with limiter: pass
    for _ in range(4): asyncio.run(run())
    with pytest.raises(ValueError): limiter.release()


def test_invalid_capacity_is_rejected():
    m = load()
    for n in (0, -1, True, 1.5):
        with pytest.raises(ValueError): m.IngestionRequestLimiter(n)
