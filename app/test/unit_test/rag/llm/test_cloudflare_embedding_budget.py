"""Request-budget regressions using synthetic inputs and no external services."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import numpy as np
import pytest
from openai import BadRequestError

from common import settings
from common.token_utils import num_tokens_from_string
from rag.llm.embedding_model import EmbeddingError, OpenAIEmbed, OpenAI_APIEmbed


@pytest.fixture(autouse=True)
def defaults(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_SIZE", 32)
    monkeypatch.setattr(settings, "EMBEDDING_REQUEST_TOKEN_LIMIT", 0)


def limit_error(message="AiError: Max context reached: requested 122148 tokens, model supports only 60000"):
    return BadRequestError(message, response=httpx.Response(400, request=httpx.Request("POST", "https://api.cloudflare.com")), body={"error": message})


def adapter(cls=OpenAI_APIEmbed, host="api.cloudflare.com", model="@cf/baai/bge-m3"):
    embed = cls("synthetic", model, base_url=f"https://{host}/client/v4/accounts/test/ai/v1")
    embed.client = MagicMock()

    def success(*, input, **kwargs):
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[float(sum(map(ord, text))), float(len(text))]) for i, text in reversed(list(enumerate(input)))], usage=SimpleNamespace(total_tokens=len(input)))

    embed.client.embeddings.create.side_effect = success
    return embed, success


@pytest.mark.parametrize("cls", [OpenAIEmbed, OpenAI_APIEmbed])
@pytest.mark.parametrize("budget", [0, 40000, 100000])
def test_cloudflare_default_ceiling_and_explicit_budget(monkeypatch, cls, budget):
    monkeypatch.setattr(settings, "EMBEDDING_REQUEST_TOKEN_LIMIT", budget)
    embed, _ = adapter(cls)
    texts = ["word " * 4000] * 32
    vectors, tokens = embed.encode(texts)
    calls = [c.kwargs["input"] for c in embed.client.embeddings.create.call_args_list]
    assert len(calls) > 1
    assert all(sum(num_tokens_from_string(t) for t in batch) <= min(budget or 60000, 60000) for batch in calls)
    assert [t for batch in calls for t in batch] == texts
    assert vectors.shape == (32, 2)
    assert tokens == 32


@pytest.mark.parametrize("host,model,split", [("gateway.ai.cloudflare.com", "@cf/baai/bge-m3", True), ("api.cloudflare.com.evil.invalid", "@cf/baai/bge-m3", False), ("api.cloudflare.com", "different-model", False)])
def test_ceiling_is_scoped_to_known_provider_model(host, model, split):
    embed, _ = adapter(host=host, model=model)
    embed.encode(["word " * 4000] * 32)
    assert (embed.client.embeddings.create.call_count > 1) is split


def test_provider_tokenizer_mismatch_splits_without_losing_order_or_usage():
    embed, success = adapter()
    texts = [f"body {i}" for i in range(32)]

    def create(*, input, **kwargs):
        if len(input) > 7:
            raise limit_error()
        return success(input=input)

    embed.client.embeddings.create.side_effect = create
    vectors, tokens = embed.encode(texts)
    np.testing.assert_array_equal(vectors, [[sum(map(ord, t)), len(t)] for t in texts])
    assert tokens == 32
    assert embed.client.embeddings.create.call_count == 15


@pytest.mark.parametrize("message", ["invalid model", "rate limited", "maximum input length exceeded"])
def test_other_bad_requests_are_not_split(message):
    embed, _ = adapter()
    embed.client.embeddings.create.side_effect = limit_error(message)
    with pytest.raises(EmbeddingError):
        embed.encode(["one", "two"])
    assert embed.client.embeddings.create.call_count == 1


def test_single_input_limit_failure_stops_without_truncating_again():
    embed, _ = adapter()
    embed.client.embeddings.create.side_effect = limit_error()
    with pytest.raises(EmbeddingError):
        embed.encode(["one"])
    assert embed.client.embeddings.create.call_count == 1


def test_split_does_not_hide_child_dimension_mismatch():
    embed, success = adapter()

    def create(*, input, **kwargs):
        if len(input) > 1:
            raise limit_error()
        result = success(input=input)
        if input == ["two"]:
            result.data[0].embedding = [1.0]
        return result

    embed.client.embeddings.create.side_effect = create
    with pytest.raises(EmbeddingError):
        embed.encode(["one", "two"])
