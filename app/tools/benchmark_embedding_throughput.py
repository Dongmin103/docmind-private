"""Offline comparison of service -> LLMBundle -> compatible adapter throughput.

Run from app/: python tools/benchmark_embedding_throughput.py
Only generated text and an in-process synthetic endpoint are used. No SDK call
reaches a network, DB, source document or running service.
"""

import asyncio
import json
import logging
import statistics
import time
from types import SimpleNamespace

import numpy as np

from common import settings
from common.misc_utils import thread_pool_exec
from common.token_utils import truncate
from rag.svr.task_executor_refactor.embedding_service import EmbeddingService
from test.unit_test.rag.llm.embedding_throughput_fakes import SyntheticEndpoint, synthetic_bundle


async def original_baseline(docs, bundle, limiter):
    # Reproduce the pre-change title + 16-body schedule with the same real
    # bundle/adapter and boundary fake used by the current service.
    async with limiter:
        title, tokens = await thread_pool_exec(bundle.encode, ["Title"])
    vectors = []
    for start in range(0, len(docs), 16):
        inputs = [truncate(doc["content_with_weight"], bundle.max_length - 10) for doc in docs[start : start + 16]]
        async with limiter:
            batch, used = await thread_pool_exec(bundle.encode, inputs)
        vectors.append(batch)
        tokens += used
    mixed = 0.1 * np.tile(title[0], (len(docs), 1)) + 0.9 * np.vstack(vectors)
    for doc, vector in zip(docs, mixed):
        doc["q_2_vec"] = vector.tolist()
    return tokens, 2


async def benchmark():
    chunk_count, delay, repetitions = 255, 0.020, 3
    # Warm tokenizer/import caches outside measured runs.
    synthetic_bundle(SyntheticEndpoint()).encode(["warmup"])
    variants = [
        ("original16_separate", 16, 1, 1, True),
        ("merged16", 16, 1, 1, False),
        ("merged32", 32, 1, 1, False),
        ("merged64", 64, 1, 1, False),
        ("merged32_concurrency2_shared1", 32, 2, 1, False),
        ("merged32_concurrency2_shared2", 32, 2, 2, False),
        ("merged64_concurrency2_shared2", 64, 2, 2, False),
    ]
    expected_vectors = None
    expected_tokens = None
    rows = []
    saved_options = settings.EMBEDDING_BATCH_SIZE, settings.EMBEDDING_BATCH_CONCURRENCY, settings.EMBEDDING_REQUEST_TOKEN_LIMIT
    try:
        for label, batch_size, concurrency, shared_capacity, baseline in variants:
            settings.EMBEDDING_BATCH_SIZE = batch_size
            settings.EMBEDDING_BATCH_CONCURRENCY = concurrency
            settings.EMBEDDING_REQUEST_TOKEN_LIMIT = 0
            durations = []
            for _ in range(repetitions):
                endpoint = SyntheticEndpoint(delay)
                bundle = synthetic_bundle(endpoint)
                docs = [{"docnm_kwd": "Title", "content_with_weight": f"body {i}"} for i in range(chunk_count)]
                ctx = SimpleNamespace(embed_limiter=asyncio.Semaphore(shared_capacity), progress_cb=None)
                start = time.perf_counter()
                if baseline:
                    tokens, dimension = await original_baseline(docs, bundle, ctx.embed_limiter)
                else:
                    tokens, dimension = await EmbeddingService(ctx).embed_chunks(docs, bundle)
                durations.append(time.perf_counter() - start)
                vectors = np.asarray([doc["q_2_vec"] for doc in docs])
                if expected_vectors is None:
                    expected_vectors, expected_tokens = vectors, tokens
                np.testing.assert_array_equal(vectors, expected_vectors)
                assert tokens == expected_tokens == 766
                assert dimension == 2
                assert endpoint.peak == min(concurrency, shared_capacity)
                assert [text for batch in endpoint.calls for text in batch].count("Title") == 1
                assert sum(map(len, endpoint.calls)) == chunk_count + 1
            rows.append(
                {
                    "variant": label,
                    "batch_size": batch_size,
                    "per_document_concurrency": concurrency,
                    "shared_capacity": shared_capacity,
                    "provider_calls": len(endpoint.calls),
                    "max_in_flight": endpoint.peak,
                    "used_tokens": tokens,
                    "median_seconds": round(statistics.median(durations), 6),
                    "runs_seconds": [round(value, 6) for value in durations],
                    "vectors_exactly_equal": True,
                }
            )
    finally:
        settings.EMBEDDING_BATCH_SIZE, settings.EMBEDDING_BATCH_CONCURRENCY, settings.EMBEDDING_REQUEST_TOKEN_LIMIT = saved_options
    print(
        json.dumps(
            {
                "chunks": chunk_count,
                "synthetic_delay_seconds_per_request": delay,
                "repetitions": repetitions,
                "provider": "in-process fake behind real OpenAI_APIEmbed/LLMBundle/EmbeddingService",
                "live_api": False,
                "results": rows,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.WARNING)
    asyncio.run(benchmark())
