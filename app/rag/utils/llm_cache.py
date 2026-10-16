# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License

"""Shared ordinary chat limiter and Redis completion/tag caches."""
import json
import os
import xxhash
from common.asyncio_utils import LoopLocalSemaphore
from rag.utils.redis_conn import REDIS_CONN

chat_limiter = LoopLocalSemaphore(int(os.environ.get("MAX_CONCURRENT_CHATS", 10)))

def get_llm_cache(llmnm, txt, history, genconf):
    """Return a cached LLM completion for the given model/text/history/config, or None on miss."""
    hasher = xxhash.xxh64()
    hasher.update((str(llmnm) + str(txt) + str(history) + str(genconf)).encode("utf-8"))

    k = hasher.hexdigest()
    bin = REDIS_CONN.get(k)
    if not bin:
        return None
    return bin


def set_llm_cache(llmnm, txt, v, history, genconf):
    """Store an LLM completion *v* in Redis keyed by a hash of model/text/history/config."""
    hasher = xxhash.xxh64()
    hasher.update((str(llmnm) + str(txt) + str(history) + str(genconf)).encode("utf-8"))
    k = hasher.hexdigest()
    REDIS_CONN.set(k, v.encode("utf-8"), 24 * 3600)


def get_tags_from_cache(kb_ids):
    """Return cached tag data for the given kb_ids from Redis, or None on miss."""
    hasher = xxhash.xxh64()
    hasher.update(str(kb_ids).encode("utf-8"))

    k = hasher.hexdigest()
    bin = REDIS_CONN.get(k)
    if not bin:
        return
    return bin


def set_tags_to_cache(kb_ids, tags):
    """Persist tag data for *kb_ids* in Redis."""
    hasher = xxhash.xxh64()
    hasher.update(str(kb_ids).encode("utf-8"))

    k = hasher.hexdigest()
    REDIS_CONN.set(k, json.dumps(tags).encode("utf-8"), 600)
