"""Regression tests with real asyncio, numpy, threads, bundle and adapter code."""

import asyncio
import logging
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from test.unit_test.rag.llm.conftest import _install_rag_llm_stub

# Reuse the provider tests' bootstrap before importing real embedding code;
# standalone collection must not depend on another suite loading it first.
_install_rag_llm_stub()

from common import settings
from common.token_utils import num_tokens_from_string, truncate
from rag.llm.embedding_model import AstraflowEmbed
from rag.svr.task_executor_refactor.embedding_service import EmbeddingService
from test.unit_test.rag.llm.embedding_throughput_fakes import SyntheticEndpoint, synthetic_bundle


def context(capacity=2):
    progress = []
    return SimpleNamespace(embed_limiter=asyncio.Semaphore(capacity), progress_cb=lambda **kw: progress.append(kw["prog"]), progress=progress)


def documents(count):
    return [{"docnm_kwd": "Title", "content_with_weight": f"body {i}"} for i in range(count)]


@pytest.fixture(autouse=True)
def defaults(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_SIZE", 16)
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_CONCURRENCY", 1, raising=False)
    monkeypatch.setattr(settings, "EMBEDDING_REQUEST_TOKEN_LIMIT", 0, raising=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("batch_size,requests", [(16, 5), (32, 3), (64, 2), (256, 2)])
async def test_effective_provider_batching_merges_title_and_preserves_vectors(monkeypatch, batch_size, requests):
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_SIZE", batch_size)
    endpoint = SyntheticEndpoint()
    docs = documents(64)
    ctx = context()
    tokens, dimension = await EmbeddingService(ctx).embed_chunks(docs, synthetic_bundle(endpoint))
    assert len(endpoint.calls) == requests
    assert endpoint.calls[0][0] == "Title"
    assert max(map(len, endpoint.calls)) <= min(batch_size, 64)
    assert [t for batch in endpoint.calls for t in batch] == ["Title"] + [f"body {i}" for i in range(64)]
    assert tokens == 193  # title1 + each body3 tokens
    assert dimension == 2
    np.testing.assert_array_equal([d["q_2_vec"] for d in docs], [[514.0 * 0.1 + sum(map(ord, f"body {i}")) * 0.9, 5 * 0.1 + len(f"body {i}") * 0.9] for i in range(64)])
    assert ctx.progress == sorted(ctx.progress)
    assert ctx.progress[-1] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_title_retains_bundle_truncation_while_body_retains_service_truncation():
    endpoint = SyntheticEndpoint()
    title = "title " * 150
    body = "body " * 150
    docs = [{"docnm_kwd": title, "content_with_weight": body}]
    tokens, _ = await EmbeddingService(context()).embed_chunks(docs, synthetic_bundle(endpoint, max_length=100))
    assert endpoint.calls == [[truncate(title, 95), truncate(body, 90)]]
    assert tokens == sum(num_tokens_from_string(t) for t in endpoint.calls[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("weight", [0, 0.5, 1])
async def test_title_mixing_keeps_existing_zero_weight_fallback(weight):
    endpoint = SyntheticEndpoint()
    docs = documents(1)
    await EmbeddingService(context()).embed_chunks(docs, synthetic_bundle(endpoint), {"filename_embd_weight": weight})
    effective = weight or 0.1
    expected = effective * np.array([514.0, 5.0]) + (1 - effective) * np.array([510.0, 6.0])
    np.testing.assert_array_equal(docs[0]["q_2_vec"], expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("shared_capacity,peak", [(1, 1), (2, 2)])
async def test_concurrency_requires_shared_capacity_and_preserves_completion_order(monkeypatch, shared_capacity, peak):
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_SIZE", 2)
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_CONCURRENCY", 2)
    endpoint = SyntheticEndpoint(behavior=lambda index, _: time.sleep(0.04 if index == 0 else 0.01))
    docs = documents(5)
    ctx = context(shared_capacity)
    tokens, _ = await EmbeddingService(ctx).embed_chunks(docs, synthetic_bundle(endpoint))
    assert endpoint.peak == peak
    assert tokens == 16
    assert [d["q_2_vec"][1] for d in docs] == [5.9] * 5
    np.testing.assert_allclose([d["q_2_vec"][0] for d in docs], [510.4, 511.3, 512.2, 513.1, 514.0])
    assert ctx.progress == sorted(ctx.progress)


@pytest.mark.asyncio
async def test_unsupported_subclass_retains_separate_title_and_inner_limit(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_SIZE", 64)
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_CONCURRENCY", 2)
    endpoint = SyntheticEndpoint(0.005)
    await EmbeddingService(context()).embed_chunks(documents(32), synthetic_bundle(endpoint, AstraflowEmbed))
    assert [len(batch) for batch in endpoint.calls] == [1, 16, 16]
    assert endpoint.peak == 1


@pytest.mark.asyncio
async def test_langfuse_bundle_serializes_and_keeps_observations(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_SIZE", 2)
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_CONCURRENCY", 2)
    endpoint = SyntheticEndpoint(0.01)
    bundle = synthetic_bundle(endpoint)
    bundle.langfuse = MagicMock()
    bundle.trace_context = {}
    bundle.langfuse_session_id = None
    await EmbeddingService(context()).embed_chunks(documents(5), bundle)
    assert endpoint.peak == 1
    assert bundle.langfuse.start_observation.call_count == 3
    assert bundle.langfuse.start_observation.return_value.end.call_count == 3


@pytest.mark.asyncio
async def test_failure_stops_new_batches_drains_running_threads_and_attaches_nothing(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_SIZE", 2)
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_CONCURRENCY", 2)
    second_started = threading.Event()
    second_finished = threading.Event()

    def behavior(index, _):
        if index == 0:
            assert second_started.wait(1)
            raise RuntimeError("synthetic failure")
        second_started.set()
        time.sleep(0.04)
        second_finished.set()

    endpoint = SyntheticEndpoint(behavior=behavior)
    docs = documents(20)
    with pytest.raises(Exception, match="synthetic failure"):
        await EmbeddingService(context()).embed_chunks(docs, synthetic_bundle(endpoint))
    assert len(endpoint.calls) == 2
    assert second_finished.is_set()
    assert endpoint.active == 0
    assert all("q_2_vec" not in d for d in docs)


@pytest.mark.asyncio
async def test_repeated_cancellation_keeps_limiter_until_sync_call_ends(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_SIZE", 2)
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_CONCURRENCY", 2)
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    endpoint = SyntheticEndpoint(behavior=lambda *_: (loop.call_soon_threadsafe(started.set), release.wait(2)))
    docs = documents(20)
    ctx = context(1)
    task = asyncio.create_task(EmbeddingService(ctx).embed_chunks(docs, synthetic_bundle(endpoint)))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        assert ctx.embed_limiter.locked()
        assert endpoint.active == 1
        assert len(endpoint.calls) == 1
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not ctx.embed_limiter.locked()
    assert endpoint.active == 0
    assert all("q_2_vec" not in d for d in docs)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [[], [[1.0, 2.0]], [[1.0, 2.0], [3.0]], [[1.0, 2.0], [float("nan"), 3.0]]])
async def test_malformed_vectors_never_attach_partial_results(bad):
    class BadModel:
        max_length = 8192
        embedding_batch_size = 2
        supports_combined_embedding = True
        supports_concurrent_embedding = True

        def encode(self, texts):
            return bad, 1

    docs = documents(1)
    with pytest.raises(ValueError):
        await EmbeddingService(context()).embed_chunks(docs, BadModel())
    assert "q_2_vec" not in docs[0]


@pytest.mark.parametrize("value", [0, -1, True, "16"])
def test_invalid_service_batch_sizes_fail_before_embedding(value):
    with pytest.raises(ValueError):
        EmbeddingService(context(), embedding_batch_size=value)


@pytest.mark.asyncio
async def test_disabled_debug_metadata_does_not_tokenize_again(monkeypatch, caplog):
    from rag.svr.task_executor_refactor import embedding_service

    def unexpected_token_count(_):
        raise AssertionError("debug-only token counting on the normal path")

    monkeypatch.setattr(embedding_service, "num_tokens_from_string", unexpected_token_count)
    with caplog.at_level(logging.INFO):
        docs = documents(1)
        await EmbeddingService(context()).embed_chunks(docs, synthetic_bundle(SyntheticEndpoint()))
    assert "q_2_vec" in docs[0]


@pytest.mark.asyncio
async def test_unsupported_provider_keeps_title_and_body_dtype_rounding():
    class MixedDtypeModel:
        max_length = 8192

        def encode(self, texts):
            if texts == ["Title"]:
                return np.array([[1., 2.]], dtype=np.float64), 1
            return np.array([[1.234567, 2.345678]], dtype=np.float32), 1

    docs = documents(1)
    await EmbeddingService(context()).embed_chunks(docs, MixedDtypeModel())
    expected = .1 * np.array([1., 2.], dtype=np.float64) + .9 * np.array([1.234567, 2.345678], dtype=np.float32)
    np.testing.assert_array_equal(docs[0]["q_2_vec"], expected)
