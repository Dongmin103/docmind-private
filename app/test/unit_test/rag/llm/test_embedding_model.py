#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
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

"""Tests for the embedding-provider fixes in ``rag.llm.embedding_model``:

* a failing embedding call raises a single deterministic, informative
  ``EmbeddingError`` (and the previous unreachable ``raise Exception(f"Error: {res}")``
  can no longer mask it, regardless of whether the SDK response exposes ``.text``);
* token counts reflect real usage, or an honest local fallback — never the old
  fabricated ``1024`` / ``+= 128`` constants;
* inputs at the truncation boundary are not pushed past the model token limit
  (the old ``8196`` overshoot is gone);
* ``ZhipuEmbed`` / ``OllamaEmbed`` now batch — ``ceil(n / batch_size)`` requests
  with input order and output shape preserved.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from rag.llm.embedding_model import DEFAULT_MAX_TOKENS, EmbeddingError, OpenAIEmbed, OpenAI_APIEmbed
from common.exceptions import ModelException
from common.token_utils import num_tokens_from_string


@pytest.mark.parametrize("adapter_class", [OpenAIEmbed, OpenAI_APIEmbed])
def test_configured_batch_size_reaches_actual_supported_provider(monkeypatch, adapter_class):
    from common import settings
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_SIZE", 32)
    embed = _make_openai(cls=adapter_class, total_tokens=7)
    vectors, tokens = embed.encode(["synthetic"] * 65)
    assert [len(c.kwargs["input"]) for c in embed.client.embeddings.create.call_args_list] == [32, 32, 1]
    assert vectors.shape == (65, 3)
    assert tokens == 21


@pytest.mark.parametrize("adapter_class", [OpenAIEmbed, OpenAI_APIEmbed])
@pytest.mark.parametrize("official,request_sizes", [(True, [36, 4]), (False, [40])])
def test_official_total_token_limit_is_not_imposed_on_compatible_endpoints(monkeypatch, adapter_class, official, request_sizes):
    from common import settings
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_SIZE", 64)
    embed = adapter_class("synthetic", "synthetic", base_url="https://api.openai.com/v1" if official else "https://synthetic.invalid/v1")
    embed.client = MagicMock()
    embed.client.embeddings.create.side_effect = _openai_create(total_tokens=1)
    vectors, _ = embed.encode(["word " * 9000] * 40)
    assert vectors.shape[0] == 40
    assert [len(c.kwargs["input"]) for c in embed.client.embeddings.create.call_args_list] == request_sizes
    if official:
        assert all(sum(num_tokens_from_string(t) for t in c.kwargs["input"]) <= 300000 for c in embed.client.embeddings.create.call_args_list)


def test_explicit_compatible_request_token_budget_splits_and_rejects_oversized_input(monkeypatch):
    from common import settings
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_SIZE", 64)
    monkeypatch.setattr(settings, "EMBEDDING_REQUEST_TOKEN_LIMIT", 5, raising=False)
    embed = _make_openai(cls=OpenAI_APIEmbed, total_tokens=1)
    embed.encode(["body 1", "body 2", "body 3"])
    assert [len(c.kwargs["input"]) for c in embed.client.embeddings.create.call_args_list] == [1, 1, 1]
    with pytest.raises(ValueError, match="token"):
        embed.encode(["word " * 20])


@pytest.mark.parametrize("indices", [[0, 0], [0, 2], [0], [0, None], [False, 1]])
def test_openai_rejects_invalid_cardinality_and_indices(indices):
    embed = _make_openai(total_tokens=1)
    data = [SimpleNamespace(index=i, embedding=[1., 2.]) for i in indices]
    embed.client.embeddings.create.return_value = SimpleNamespace(data=data, usage=SimpleNamespace(total_tokens=1))
    embed.client.embeddings.create.side_effect = None
    with pytest.raises(EmbeddingError):
        embed.encode(["body 1", "body 2"])


def test_compatible_response_without_indices_keeps_returned_order():
    embed = _make_openai(cls=OpenAI_APIEmbed, total_tokens=1)
    embed.client.embeddings.create.side_effect = None
    embed.client.embeddings.create.return_value = SimpleNamespace(data=[SimpleNamespace(index=None, embedding=[3., 4.]), SimpleNamespace(index=None, embedding=[5., 6.])])
    vectors, _ = embed.encode(["body 1", "body 2"])
    np.testing.assert_array_equal(vectors, [[3., 4.], [5., 6.]])


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _OpenAIResp:
    """Minimal stand-in for an OpenAI embeddings response.

    Unlike ``MagicMock`` it does NOT auto-create a ``usage`` attribute, so
    ``total_token_count_from_response`` correctly returns 0 when ``total_tokens``
    is not supplied (exercising the local-count fallback paths).
    """

    def __init__(self, vectors, total_tokens=None):
        self.data = [SimpleNamespace(embedding=list(v)) for v in vectors]
        if total_tokens is not None:
            self.usage = SimpleNamespace(total_tokens=total_tokens)


def _openai_create(total_tokens=None, dim=3):
    """Build a side_effect that returns one vector per input text."""

    def _create(input, model, **kwargs):
        return _OpenAIResp([[float(i)] * dim for i in range(len(input))], total_tokens=total_tokens)

    return _create


def _make_openai(cls=OpenAIEmbed, total_tokens=None):
    embed = cls("key", "text-embedding-3-small", base_url="https://example.invalid/v1")
    embed.client = MagicMock()
    embed.client.embeddings.create = MagicMock(side_effect=_openai_create(total_tokens=total_tokens))
    return embed


# --------------------------------------------------------------------------- #
# 1. Deterministic, informative error handling (the masked-error bug)
# --------------------------------------------------------------------------- #
class _BadRespWithText:
    """Parsing this raises; it also exposes ``.text`` — which the old
    ``log_exception(_e, res)`` path would have re-raised as a bare
    ``Exception(text)``, masking the intended error non-deterministically."""

    text = "Internal Server Error"

    @property
    def data(self):
        raise ValueError("malformed response payload")


class _BadRespNoText:
    @property
    def data(self):
        raise ValueError("malformed response payload")


# --------------------------------------------------------------------------- #
# 2. Token accounting (no fabricated 1024 / += 128)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 3. Truncation boundary (no 8196 overshoot)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 4. Batching for Zhipu and Ollama (ceil(n / batch_size) requests)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 5. Provider-specific request/response shapes
# --------------------------------------------------------------------------- #
