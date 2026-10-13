"""Process-wide admission for cloud ingestion's independent event loops."""
import asyncio
from collections import deque
import os
import threading


class IngestionRequestLimiter:
    """FIFO, cancellation-safe permits without binding to any asyncio loop.

    Polling never holds a permit in an abandoned executor thread. Only the
    waiting coroutine can acquire it. Scope is this API process, not a cluster.
    """

    def __init__(self, capacity: int):
        if type(capacity) is not int or capacity < 1:
            raise ValueError('capacity must be a positive integer')
        self._capacity = capacity
        self._available = capacity
        self._lock = threading.Lock()
        self._waiters = deque()

    async def acquire(self) -> bool:
        waiter = object()
        with self._lock:
            self._waiters.append(waiter)
        try:
            while True:
                with self._lock:
                    if self._waiters[0] is waiter and self._available:
                        self._waiters.popleft()
                        self._available -= 1
                        return True
                await asyncio.sleep(.01)
        except BaseException:
            with self._lock:
                self._waiters.remove(waiter)
            raise

    def release(self) -> None:
        with self._lock:
            if self._available == self._capacity:
                raise ValueError('permit released without acquisition')
            self._available += 1

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.release()
        return False


embedding_requests = IngestionRequestLimiter(int(os.environ.get('MAX_CONCURRENT_EMBEDDING_REQUESTS', '2')))
