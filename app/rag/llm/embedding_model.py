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
#
import time
from abc import ABC
from urllib.parse import urlparse

import numpy as np
from openai import BadRequestError, OpenAI

from common import settings
from common.exceptions import ModelException
from common.token_utils import num_tokens_from_string, truncate, total_token_count_from_response
from rag.utils.url_utils import ensure_v1
import logging

logger = logging.getLogger(__name__)

# Standard token ceiling for the common 8K-context embedding models (OpenAI
# text-embedding-*, Mistral, Bedrock Titan, ...). Inputs are truncated to this
# many tokens so boundary-sized chunks are not rejected by the provider.
DEFAULT_MAX_TOKENS = 8192


class EmbeddingError(ModelException):
    """Raised when an embedding provider fails to return usable embeddings.

    A single, deterministic exception type for every provider failure path so
    callers see consistent behaviour regardless of which SDK raised underneath.
    Subclasses ``ModelException`` so the API error handler (and its retry
    semantics) treats embedding failures like any other model failure.
    """


def _sorted_by_index(items):
    """Order OpenAI-style SDK embedding items by their ``.index`` so batched
    results stay aligned with input order even if the provider returns them out
    of order. Stable no-op when items carry no ``index`` attribute."""
    return sorted(items, key=lambda d: getattr(d, "index", 0))


def _raise_model_exception_if_failed(resp):
    status_code = resp.status_code
    if status_code >= 400:
        if status_code < 500 and status_code not in [408, 429]:
            raise ModelException(f"status: {resp.status_code}, response: {resp.text}", retryable=False)
        raise ModelException(f"status: {resp.status_code}, response: {resp.text}", retryable=True)


class Base(ABC):
    def __init__(self, key, model_name, **kwargs):
        """
        Constructor for abstract base class.
        Parameters are accepted for interface consistency but are not stored.
        Subclasses should implement their own initialization as needed.
        """
        pass

    def encode(self, texts: list):
        raise NotImplementedError("Please implement encode method!")

    def encode_queries(self, text: str):
        raise NotImplementedError("Please implement encode method!")

    def _batched_encode(self, texts: list, call_fn, *, batch_size: int, truncate_to: int | None = None):
        """Drive an embedding provider over ``texts`` in batches.

        This is the shared template behind the OpenAI-style providers. It owns:

        * optional per-text truncation to ``truncate_to`` tokens (skipped when
          ``None``) so oversized inputs do not get rejected by the provider;
        * the batch loop, issuing ``ceil(len(texts) / batch_size)`` calls;
        * accumulation of the per-text vectors into a single ``np.ndarray``;
        * summation of the per-batch token counts;
        * one deterministic, informative error path.

        ``call_fn`` is a provider-supplied closure ``call_fn(batch) ->
        (embeddings, token_count)``. It performs the SDK/HTTP request *and*
        parses the response (so a malformed/error response surfaces here), and
        must not assume any particular response shape — the helper never touches
        the raw response object. ``embeddings`` is a sequence of per-text
        vectors; ``token_count`` is the real token usage for that batch.

        Any exception raised by ``call_fn`` is wrapped in a single
        :class:`EmbeddingError` that includes the underlying detail. We log and
        raise here directly instead of relying on ``log_exception``'s implicit
        raise (whose surfaced exception varies by SDK response shape).
        """
        if truncate_to is not None:
            texts = [truncate(t, truncate_to) for t in texts]
        vectors = []
        token_count = 0
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            try:
                embeddings, tokens = call_fn(batch)
            except ModelException:
                # Already a structured (and possibly retryable) model error; keep it.
                raise
            except Exception as e:
                logger.exception("%s embedding request failed", type(self).__name__)
                raise EmbeddingError(f"Embedding request failed for {type(self).__name__}. Error: {e}") from e
            vectors.extend(embeddings)
            token_count += tokens
        return np.array(vectors), token_count

    @staticmethod
    def _openai_http_embeddings(response):
        """Parse an OpenAI-compatible HTTP embeddings ``requests`` response.

        Returns ``(embeddings, token_count)``. Raises a retryable-aware
        :class:`ModelException` on a bad HTTP status, or surfaces the response
        body (via :class:`EmbeddingError`) when the payload is not a successful
        ``{"data": [...]}`` response.
        """
        _raise_model_exception_if_failed(response)
        res = response.json()
        if not isinstance(res, dict) or "data" not in res:
            raise ValueError(f"unexpected embeddings response (status {getattr(response, 'status_code', '?')}): {res}")
        # Keep results aligned with input order: OpenAI-compatible responses carry
        # a per-item `index`; sorting by it is a no-op (stable) when it is absent.
        data = sorted(res["data"], key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in data], total_token_count_from_response(res)


class OpenAIEmbed(Base):
    _FACTORY_NAME = "OpenAI"

    @property
    def _is_cloudflare_bge_m3(self):
        return self.model_name == "@cf/baai/bge-m3" and urlparse(self.base_url).hostname in ("api.cloudflare.com", "gateway.ai.cloudflare.com")

    def _call_with_context_split(self, batch):
        """Split only a confirmed Cloudflare context rejection, without losing inputs.

        The local cl100k estimate is not the provider's tokenizer. Each retry
        strictly reduces the number of inputs; a rejected singleton terminates.
        Splits stay sequential inside the caller's existing concurrency permit.
        """
        try:
            return self._call(batch)
        except BadRequestError as exc:
            detail = str(exc).lower()
            if not (self._is_cloudflare_bge_m3 and len(batch) > 1 and "max context reached" in detail and "model supports only" in detail):
                raise
        logger.info("embedding provider context limit: splitting input_count=%d", len(batch))
        middle = len(batch) // 2
        left, left_tokens = self._call_with_context_split(batch[:middle])
        right, right_tokens = self._call_with_context_split(batch[middle:])
        return np.vstack((left, right)), left_tokens + right_tokens

    @property
    def supports_combined_embedding(self):
        # Subclasses with a different call/preprocessing contract stay serial
        # and retain their existing 16-input adapter limit.
        return type(self) in (OpenAIEmbed, OpenAI_APIEmbed)

    @property
    def supports_concurrent_embedding(self):
        return self.supports_combined_embedding

    @property
    def embedding_batch_size(self):
        if not self.supports_combined_embedding:
            return 16
        size = settings.EMBEDDING_BATCH_SIZE
        if not isinstance(size, int) or isinstance(size, bool) or size < 1:
            raise ValueError("EMBEDDING_BATCH_SIZE must be a positive integer")
        return min(size, 64)

    def __init__(self, key, model_name="text-embedding-ada-002", base_url="https://api.openai.com/v1"):
        if not base_url:
            base_url = "https://api.openai.com/v1"
        self.base_url = ensure_v1(base_url)
        self.client = OpenAI(api_key=key, base_url=self.base_url)
        self.model_name = model_name

    def _call(self, batch):
        res = self.client.embeddings.create(input=batch, model=self.model_name, encoding_format="float", extra_body={"drop_params": True})
        if len(res.data) != len(batch):
            raise ValueError(f"Embedding response count {len(res.data)} does not match input count {len(batch)}")
        indices = [getattr(item, "index", None) for item in res.data]
        data = res.data
        if any(index is not None for index in indices):
            if any(type(index) is not int for index in indices) or sorted(indices) != list(range(len(batch))):
                raise ValueError("Embedding response indices must cover every input exactly once")
            data = _sorted_by_index(data)
        vectors = np.asarray([d.embedding for d in data])
        if vectors.ndim != 2 or vectors.shape[1] == 0 or not np.issubdtype(vectors.dtype, np.number) or not np.isfinite(vectors).all():
            raise ValueError("Embedding response must contain finite vectors with a consistent dimension")
        return vectors, total_token_count_from_response(res)

    def encode(self, texts: list):
        if not self.supports_combined_embedding:
            return self._batched_encode(texts, self._call, batch_size=16, truncate_to=8191)
        # Preserve per-input truncation. Known provider/model ceilings apply
        # even when the configured budget is unset or larger than the ceiling.
        token_limit = settings.EMBEDDING_REQUEST_TOKEN_LIMIT
        if type(token_limit) is not int or token_limit < 0:
            raise ValueError("EMBEDDING_REQUEST_TOKEN_LIMIT must be a nonnegative integer")
        if urlparse(self.base_url).hostname == "api.openai.com":
            token_limit = min(token_limit or 300000, 300000)
        elif self._is_cloudflare_bge_m3:
            token_limit = min(token_limit or 60000, 60000)
        count_tokens = token_limit or logger.isEnabledFor(logging.DEBUG)
        texts = [truncate(text, 8191) for text in texts]
        batches = []
        batch = []
        batch_tokens = 0
        for text in texts:
            tokens = num_tokens_from_string(text) if count_tokens else 0
            if token_limit and tokens > token_limit:
                raise ValueError("Embedding input exceeds configured request token limit")
            if batch and (len(batch) >= self.embedding_batch_size or (token_limit and batch_tokens + tokens > token_limit)):
                batches.append((batch, batch_tokens))
                batch, batch_tokens = [], 0
            batch.append(text)
            batch_tokens += tokens
        if batch:
            batches.append((batch, batch_tokens))
        vectors = []
        used_tokens = 0
        dimension = None
        for batch, input_tokens in batches:
            start = time.perf_counter()
            result, tokens = self._batched_encode(batch, self._call_with_context_split, batch_size=self.embedding_batch_size)
            if dimension is not None and result.shape[1] != dimension:
                raise EmbeddingError("Embedding dimension changed between batches")
            dimension = result.shape[1]
            vectors.append(result)
            used_tokens += tokens
            logger.debug("embedding provider batch input_count=%d input_tokens_estimate=%d encode_seconds=%.6f", len(batch), input_tokens, time.perf_counter() - start)
        return (np.vstack(vectors) if vectors else np.array([])), used_tokens

    def encode_queries(self, text):
        vectors, token_count = self.encode([text])
        return vectors[0], token_count


class OpenAI_APIEmbed(OpenAIEmbed):
    _FACTORY_NAME = "OpenAI-API-Compatible"

    def __init__(self, key, model_name, base_url):
        if not base_url:
            raise ValueError("url cannot be None")
        self.base_url = ensure_v1(base_url)
        self.client = OpenAI(api_key=key, base_url=self.base_url)
        self.model_name = model_name.split("___")[0]
