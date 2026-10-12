"""Synthetic provider boundary used by throughput tests and the offline benchmark."""

import threading
import time
from types import SimpleNamespace

from api.db.services.llm_service import LLMBundle
from common.token_utils import num_tokens_from_string
from rag.llm.embedding_model import OpenAI_APIEmbed


def synthetic_vector(text):
    return [float(sum(map(ord, text))), float(len(text))]


class SyntheticEndpoint:
    def __init__(self, delay=0, behavior=None):
        self.delay = delay
        self.behavior = behavior
        self.calls = []
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()

    def create(self, *, input, model, **kwargs):
        with self.lock:
            call_index = len(self.calls)
            self.calls.append(list(input))
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.behavior:
                self.behavior(call_index, input)
            time.sleep(self.delay)
            # Providers may return rows out of order; indices are authoritative.
            data = [SimpleNamespace(index=i, embedding=synthetic_vector(t)) for i, t in enumerate(input)]
            return SimpleNamespace(data=list(reversed(data)), usage=SimpleNamespace(total_tokens=sum(num_tokens_from_string(t) for t in input)))
        finally:
            with self.lock:
                self.active -= 1


def synthetic_bundle(endpoint, adapter_class=OpenAI_APIEmbed, base_url="https://synthetic.invalid/v1", max_length=8192):
    adapter = adapter_class("synthetic-key", "@cf/baai/bge-m3", base_url=base_url)
    adapter.client = SimpleNamespace(embeddings=endpoint)
    # Keep LLMBundle.encode (normalization, truncation and usage observations) real;
    # avoid constructor DB and external Langfuse discovery in an offline test.
    bundle = object.__new__(LLMBundle)
    bundle.mdl = adapter
    bundle.max_length = max_length
    bundle.langfuse = None
    bundle.model_config = {"llm_name": "@cf/baai/bge-m3", "llm_factory": "OpenAI-API-Compatible"}
    return bundle
