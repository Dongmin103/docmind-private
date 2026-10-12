#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

"""
Embedding Service Module.

Provides [`EmbeddingService`](rag/svr/task_executor_refactor/embedding_service.py:42) for vector embedding operations.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Tuple

import numpy as np
from common import settings
from common.misc_utils import thread_pool_exec
from common.token_utils import num_tokens_from_string, truncate
from rag.svr.task_executor_refactor.embedding_utils import EmbeddingUtils
from rag.svr.task_executor_refactor.task_context import TaskContext

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Service for vector embedding operations.

    This service handles:
    - Batch encoding of text chunks
    - Title + content vector combination
    - Embedding model rate limiting

    All intermediate results are recorded via RecordingContext for comparison.
    """

    def __init__(
        self,
        ctx: TaskContext,
        embedding_batch_size: int = None,
    ):
        """Initialize EmbeddingService.

        Args:
            ctx: TaskContext containing task configuration and execution resources.
            embedding_batch_size: Batch size for embedding operations.
        """
        self._task_context = ctx

        self._embedding_batch_size = settings.EMBEDDING_BATCH_SIZE if embedding_batch_size is None else embedding_batch_size
        if type(self._embedding_batch_size) is not int or self._embedding_batch_size < 1:
            raise ValueError("embedding_batch_size must be a positive integer")

    async def embed_chunks(
        self,
        docs: List[Dict[str, Any]],
        embedding_model,
        parser_config: Dict = None,
    ) -> Tuple[int, int]:
        """Embed a list of chunks.

        Args:
            docs: List of chunk dictionaries to embed.
            embedding_model: The embedding model bundle (LLMBundle).
            parser_config: Parser configuration for filename embedding weight.

        Returns:
            Tuple of (token_count, vector_size).
        """
        if parser_config is None:
            parser_config = {}

        # Prepare text for embedding using EmbeddingUtils
        titles, contents = EmbeddingUtils.prepare_texts_for_embedding(docs)

        if not contents:
            return 0, 0

        adapter = getattr(embedding_model, "mdl", embedding_model)
        merge_title = getattr(adapter, "supports_combined_embedding", False) is True
        concurrency = settings.EMBEDDING_BATCH_CONCURRENCY
        if type(concurrency) is not int or concurrency not in (1, 2):
            raise ValueError("EMBEDDING_BATCH_CONCURRENCY must be 1 or 2")
        if getattr(adapter, "supports_concurrent_embedding", False) is not True or getattr(embedding_model, "langfuse", None):
            concurrency = 1
        batch_size = self._embedding_batch_size
        if merge_title:
            batch_size = min(batch_size, adapter.embedding_batch_size)
        # Body preprocessing remains distinct from title preprocessing. The
        # title goes directly through LLMBundle.encode, exactly as before.
        body_texts = [truncate(text, embedding_model.max_length - 10) for text in contents]
        texts = [titles[0]] + body_texts if merge_title else body_texts
        title_vectors = None
        tk_count = 0
        stopped = False
        completed_contents = 0

        async def encode_batch(batch, index):
            nonlocal stopped
            wait_start = time.perf_counter()
            async with self._task_context.embed_limiter:
                if stopped:
                    raise asyncio.CancelledError
                wait_seconds = time.perf_counter() - wait_start
                encode_start = time.perf_counter()
                call = asyncio.create_task(thread_pool_exec(embedding_model.encode, batch))
                try:
                    vectors, tokens = await asyncio.shield(call)
                except asyncio.CancelledError:
                    stopped = True
                    # Cancelling the await cannot stop the synchronous provider.
                    # Keep the limiter and event loop alive until its thread ends.
                    await self._drain(call)
                    raise
                except BaseException:
                    stopped = True
                    raise
                encode_seconds = time.perf_counter() - encode_start
                vectors = np.asarray(vectors)
                if vectors.ndim != 2 or len(vectors) != len(batch) or vectors.shape[1] == 0 or not np.issubdtype(vectors.dtype, np.number) or not np.isfinite(vectors).all():
                    stopped = True
                    raise ValueError("Embedding response must contain one finite, consistent vector per input")
                if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
                    stopped = True
                    raise ValueError("Embedding token usage must be a nonnegative integer")
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "embedding service batch index=%d input_count=%d submitted_tokens_estimate=%d limiter_wait_seconds=%.6f encode_seconds=%.6f used_tokens=%d",
                        index,
                        len(batch),
                        sum(num_tokens_from_string(t) for t in batch),
                        wait_seconds,
                        encode_seconds,
                        tokens,
                    )
                return vectors, tokens

        if not merge_title:
            title_vectors, tk_count = await encode_batch(titles[0:1], -1)

        batches = [texts[i : i + batch_size] for i in range(0, len(texts), batch_size)]
        results = [None] * len(batches)
        next_batch = 0

        async def worker():
            nonlocal next_batch, stopped, completed_contents
            try:
                while not stopped and next_batch < len(batches):
                    index = next_batch
                    next_batch += 1
                    vectors, tokens = await encode_batch(batches[index], index)
                    results[index] = (vectors, tokens)
                    completed_contents += len(batches[index]) - int(merge_title and index == 0)
                    if self._task_context.progress_cb:
                        self._task_context.progress_cb(prog=0.7 + 0.2 * completed_contents / len(contents), msg="")
            except BaseException:
                stopped = True
                raise

        workers = [asyncio.create_task(worker()) for _ in range(min(concurrency, len(batches)))]
        try:
            await asyncio.gather(*workers)
        except BaseException:
            stopped = True
            for worker_task in workers:
                worker_task.cancel()
            await self._drain(asyncio.gather(*workers, return_exceptions=True))
            raise
        vector_batches = [result[0] for result in results]
        dimensions = {vectors.shape[1] for vectors in vector_batches}
        if title_vectors is not None:
            dimensions.add(title_vectors.shape[1])
        if len(dimensions) != 1:
            raise ValueError("Embedding dimension changed between batches")
        vectors = np.vstack(vector_batches)
        tk_count += sum(result[1] for result in results)
        # Keep separate-provider title/body dtypes through the original mixing
        # operations instead of promoting the body before scalar multiplication.
        title = vectors[0] if merge_title else title_vectors[0]
        tts = np.tile(title, (len(contents), 1))
        cnts = vectors[1:] if merge_title else vectors

        # Combine title and content vectors using EmbeddingUtils
        title_weight = parser_config.get("filename_embd_weight", EmbeddingUtils.DEFAULT_TITLE_WEIGHT)
        vects = EmbeddingUtils.combine_title_content_vectors(tts, cnts, title_weight)

        if len(vects) != len(docs):
            raise ValueError("Embedding vectors/docs length mismatch")

        # Attach vectors to docs using EmbeddingUtils
        vector_size = EmbeddingUtils.attach_vectors(docs, vects)

        return tk_count, vector_size

    @staticmethod
    async def _drain(future):
        """Observe completion despite repeated cancellation; caller re-raises."""
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        # Retrieve failures even when cancellation raced with completion, so
        # asyncio never reports an unobserved thread/worker exception.
        if not future.cancelled():
            future.exception()
