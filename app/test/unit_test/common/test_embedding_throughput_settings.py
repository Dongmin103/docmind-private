"""Configuration rejects unsafe values before worker scheduling."""

import importlib

import pytest

from common import settings


@pytest.mark.parametrize(
    "name,value", [("EMBEDDING_BATCH_SIZE", "0"), ("EMBEDDING_BATCH_SIZE", "no"), ("EMBEDDING_BATCH_CONCURRENCY", "0"), ("EMBEDDING_BATCH_CONCURRENCY", "3"), ("EMBEDDING_REQUEST_TOKEN_LIMIT", "-1")]
)
def test_invalid_throughput_settings_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        settings._embedding_throughput_options()


def test_existing_large_global_batch_size_still_accepted(monkeypatch):
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "256")
    monkeypatch.setenv("EMBEDDING_BATCH_CONCURRENCY", "2")
    monkeypatch.setenv("EMBEDDING_REQUEST_TOKEN_LIMIT", "12345")
    assert settings._embedding_throughput_options() == (256, 2, 12345)


def test_shared_embed_capacity_is_independent_of_chunk_capacity(monkeypatch):
    from rag.svr import task_executor_limiter

    with monkeypatch.context() as patch:
        patch.setenv("MAX_CONCURRENT_CHUNK_BUILDERS", "1")
        patch.setenv("MAX_CONCURRENT_EMBEDDING_REQUESTS", "2")
        module = importlib.reload(task_executor_limiter)
        assert module.chunk_limiter._value == 1
        assert module.embed_limiter._value == 2
    importlib.reload(task_executor_limiter)


@pytest.mark.parametrize("value", ["0", "-1", "bad"])
def test_invalid_shared_embed_capacity_rejected(monkeypatch, value):
    from rag.svr import task_executor_limiter

    with monkeypatch.context() as patch:
        patch.setenv("MAX_CONCURRENT_EMBEDDING_REQUESTS", value)
        with pytest.raises(ValueError, match="MAX_CONCURRENT_EMBEDDING_REQUESTS"):
            importlib.reload(task_executor_limiter)
    importlib.reload(task_executor_limiter)
