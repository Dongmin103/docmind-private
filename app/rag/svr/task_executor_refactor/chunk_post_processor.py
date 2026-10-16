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
Chunk Post-Processor Module.

Provides post-processing functions for chunks:
- Keyword extraction
- Question generation
- Metadata generation
- Content tagging
"""

import asyncio
import json
import logging
import random
import re
from datetime import datetime
from timeit import default_timer as timer

from api.db.joint_services.tenant_model_service import resolve_model_config
from api.db.services.doc_metadata_service import DocMetadataService
from api.db.services.llm_service import LLMBundle
from api.db.services.task_service import (
    abort_doc_chunking_counter,
    is_doc_chunking_aborted,
    credit_doc_chunking_task,
    clear_doc_chunking_counter,
)
from common import settings
from common.constants import TAG_FLD, LLMType
from common.metadata_utils import turn2jsonschema, update_metadata_to
from rag.utils.llm_cache import get_llm_cache, get_tags_from_cache, set_llm_cache, set_tags_to_cache
from rag.nlp import rag_tokenizer
from rag.prompts.generator import content_tagging, gen_metadata, keyword_extraction, question_proposal
from rag.svr.task_executor_refactor.task_context import TaskContext


# Elasticsearch keyword fields reject terms whose UTF-8 encoding exceeds
# 32766 bytes. Split oversized terms so ingestion never fails because a
# malformed LLM response produced a single huge "keyword".
_ES_KEYWORD_MAX_TERM_BYTES = 32766


def _sanitize_keyword_term(term: str) -> list[str]:
    """Return keyword pieces that fit into an Elasticsearch keyword field.

    If ``term`` is small enough it is returned as-is. Otherwise it is
    truncated at a character boundary so the UTF-8 encoding never exceeds
    the ES keyword limit. This avoids corrupting multi-byte characters by
    slicing raw bytes.
    """
    term = term.strip()
    if not term:
        return []
    term_byte_length = len(term.encode("utf-8"))
    if term_byte_length <= _ES_KEYWORD_MAX_TERM_BYTES:
        return [term]

    logging.warning(
        "Sanitizing oversized keyword term (%d bytes, limit %d)",
        term_byte_length,
        _ES_KEYWORD_MAX_TERM_BYTES,
    )
    length = 0
    end = 0
    for index, character in enumerate(term):
        character_bytes = len(character.encode("utf-8"))
        if length + character_bytes > _ES_KEYWORD_MAX_TERM_BYTES:
            end = index
            break
        length += character_bytes
    else:
        end = len(term)
    truncated = term[:end].rstrip()
    if not truncated:
        return []
    return [truncated]


async def extract_keywords(docs: list[dict], ctx: TaskContext) -> None:
    """Extract keywords for chunks.

    Args:
        docs: List of chunk dictionaries to process.
        ctx: TaskContext containing task configuration.
    """
    chat_limiter = ctx.chat_limiter

    st = timer()
    ctx.progress_cb(msg="Start to generate keywords for every chunk ...")
    chat_model_config = resolve_model_config(ctx.tenant_id, LLMType.CHAT, ctx.llm_id)
    with LLMBundle(ctx.tenant_id, chat_model_config, lang=ctx.language) as chat_model:

        async def doc_keyword_extraction(chat_mdl, d, topn):
            cached = get_llm_cache(chat_mdl.llm_name, d["content_with_weight"], "keywords", {"topn": topn})
            if not cached:
                if ctx.has_canceled_func(ctx.id):
                    ctx.progress_cb(-1, msg="Task has been canceled.")
                    return
                async with chat_limiter:
                    cached = await keyword_extraction(chat_mdl, d["content_with_weight"], topn)
                set_llm_cache(chat_mdl.llm_name, d["content_with_weight"], cached, "keywords", {"topn": topn})
            if cached:
                d["important_kwd"] = [kw for k in re.split(r"[,，;；、\r\n]+", cached) for kw in _sanitize_keyword_term(k)]
                d["important_tks"] = rag_tokenizer.tokenize(" ".join(d["important_kwd"]))
            return

        tasks = []
        for doc in docs:
            tasks.append(asyncio.create_task(doc_keyword_extraction(chat_model, doc, ctx.parser_config["auto_keywords"])))
        try:
            await asyncio.gather(*tasks, return_exceptions=False)
        except Exception as e:
            logging.error(f"Error in doc_keyword_extraction: {e}")
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        ctx.progress_cb(msg=f"Keywords generation {len(docs)} chunks completed in {timer() - st:.2f}s")


async def generate_questions(docs: list[dict], ctx: TaskContext) -> None:
    """Generate questions for chunks.

    Args:
        docs: List of chunk dictionaries to process.
        ctx: TaskContext containing task configuration.
    """
    chat_limiter = ctx.chat_limiter

    st = timer()
    ctx.progress_cb(msg="Start to generate questions for every chunk ...")
    chat_model_config = resolve_model_config(ctx.tenant_id, LLMType.CHAT, ctx.llm_id)
    with LLMBundle(ctx.tenant_id, chat_model_config, lang=ctx.language) as chat_model:

        async def doc_question_proposal(chat_mdl, d, topn):
            cached = get_llm_cache(chat_mdl.llm_name, d["content_with_weight"], "question", {"topn": topn})
            if not cached:
                if ctx.has_canceled_func(ctx.id):
                    ctx.progress_cb(-1, msg="Task has been canceled.")
                    return
                async with chat_limiter:
                    cached = await question_proposal(chat_mdl, d["content_with_weight"], topn)
                set_llm_cache(chat_mdl.llm_name, d["content_with_weight"], cached, "question", {"topn": topn})
            if cached:
                d["question_kwd"] = cached.split("\n")
                d["question_tks"] = rag_tokenizer.tokenize("\n".join(d["question_kwd"]))

        tasks = []
        for doc in docs:
            tasks.append(asyncio.create_task(doc_question_proposal(chat_model, doc, ctx.parser_config["auto_questions"])))
        try:
            await asyncio.gather(*tasks, return_exceptions=False)
        except Exception as e:
            logging.error("Error in doc_question_proposal", exc_info=e)
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        ctx.progress_cb(msg=f"Question generation {len(docs)} chunks completed in {timer() - st:.2f}s")


def build_metadata_config(parser_config: dict) -> list:
    """Build the metadata configuration from parser_config.

    Extracts and normalizes ``metadata`` and ``built_in_metadata`` from the
    parser configuration into a single list or dict that is passed to the LLM
    cache and generation functions.

    This should be called once per ``generate_metadata`` invocation — the result
    is identical for every chunk within the same document parse session so
    extracting it avoids rebuilding inside the per-chunk async task.

    Args:
        parser_config: Configuration dict from the parser, expected to contain
            ``metadata`` (dict or list) and optionally ``built_in_metadata``
            (list of metadata item dicts).

    Returns:
        A list or dict representing the merged metadata configuration.
    """
    metadata_conf = parser_config.get("metadata", [])
    built_in_metadata = list(parser_config.get("built_in_metadata") or [])
    if isinstance(metadata_conf, dict):
        if not isinstance(metadata_conf.get("properties"), dict):
            metadata_conf = {"type": "object", "properties": {}}
        if built_in_metadata:
            metadata_conf = {
                **metadata_conf,
                "properties": {
                    **metadata_conf.get("properties", {}),
                    **turn2jsonschema(built_in_metadata).get("properties", {}),
                },
            }
    elif isinstance(metadata_conf, list):
        metadata_conf = metadata_conf + built_in_metadata
    else:
        metadata_conf = built_in_metadata
    return metadata_conf


async def generate_metadata(docs: list[dict], ctx: TaskContext) -> None:
    """Generate metadata for chunks.

    Args:
        docs: List of chunk dictionaries to process.
        ctx: TaskContext containing task configuration.
    """
    chat_limiter = ctx.chat_limiter

    st = timer()
    ctx.progress_cb(msg="Start to generate meta-data for every chunk ...")
    chat_model_config = resolve_model_config(ctx.tenant_id, LLMType.CHAT, ctx.llm_id)
    with LLMBundle(ctx.tenant_id, chat_model_config, lang=ctx.language) as chat_model:
        metadata_conf = build_metadata_config(ctx.parser_config)

        async def gen_metadata_task(chat_mdl, d):
            cached = get_llm_cache(chat_mdl.llm_name, d["content_with_weight"], "metadata", metadata_conf)
            if not cached:
                if ctx.has_canceled_func(ctx.id):
                    ctx.progress_cb(-1, msg="Task has been canceled.")
                    return
                async with chat_limiter:
                    cached = await gen_metadata(chat_mdl, turn2jsonschema(metadata_conf), d["content_with_weight"])
                set_llm_cache(chat_mdl.llm_name, d["content_with_weight"], cached, "metadata", metadata_conf)
            if cached:
                d["metadata_obj"] = cached

        tasks = []
        for doc in docs:
            tasks.append(asyncio.create_task(gen_metadata_task(chat_model, doc)))
        try:
            await asyncio.gather(*tasks, return_exceptions=False)
        except Exception as e:
            logging.error("Error in gen_metadata", exc_info=e)
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        metadata = {}
        for doc in docs:
            if "metadata_obj" in doc:
                metadata = update_metadata_to(metadata, doc["metadata_obj"])
                del doc["metadata_obj"]
        if metadata:
            existing_meta = DocMetadataService.get_document_metadata(ctx.doc_id)
            existing_meta = existing_meta if isinstance(existing_meta, dict) else {}
            metadata = update_metadata_to(metadata, existing_meta)
            if ctx.write_interceptor:
                ctx.write_interceptor.intercept("DocMetadataService.update_document_metadata")
            else:
                DocMetadataService.update_document_metadata(ctx.doc_id, metadata)
        ctx.progress_cb(msg=f"Metadata generation {len(docs)} chunks completed in {timer() - st:.2f}s")


def apply_built_in_metadata(ctx: TaskContext) -> None:
    built_in_meta_config = ctx.parser_config.get("built_in_metadata", [])
    if not built_in_meta_config:
        return

    built_in_meta = {}
    for item in built_in_meta_config:
        key = item.get("key", "")
        if key == "update_time":
            built_in_meta["update_time"] = str(datetime.now()).replace("T", " ")[:19]
        elif key == "file_name":
            built_in_meta["file_name"] = ctx.name
    if built_in_meta:
        existing_meta = DocMetadataService.get_document_metadata(ctx.doc_id)
        existing_meta = existing_meta if isinstance(existing_meta, dict) else {}
        existing_meta = update_metadata_to(existing_meta, built_in_meta)
        if ctx.write_interceptor:
            ctx.write_interceptor.intercept("DocMetadataService.update_document_metadata")
        else:
            DocMetadataService.update_document_metadata(ctx.doc_id, existing_meta)


async def apply_tags(docs: list[dict], ctx: TaskContext) -> None:
    """Apply tags to chunks.

    Args:
        docs: List of chunk dictionaries to process.
        ctx: TaskContext containing task configuration.
    """
    chat_limiter = ctx.chat_limiter

    ctx.progress_cb(msg="Start to tag for every chunk ...")
    kb_ids = ctx.kb_parser_config["tag_kb_ids"]
    tenant_id = ctx.tenant_id
    topn_tags = ctx.kb_parser_config.get("topn_tags", 3)
    S = 1000
    st = timer()
    examples = []
    all_tags = get_tags_from_cache(kb_ids)
    if not all_tags:
        all_tags = settings.retriever.all_tags_in_portion(tenant_id, kb_ids, S)
        set_tags_to_cache(kb_ids, all_tags)
    else:
        all_tags = json.loads(all_tags)
    chat_model_config = resolve_model_config(tenant_id, LLMType.CHAT, ctx.llm_id)
    with LLMBundle(ctx.tenant_id, chat_model_config, lang=ctx.language) as chat_model:
        docs_to_tag = []
        for doc in docs:
            if ctx.has_canceled_func(ctx.id):
                ctx.progress_cb(-1, msg="Task has been canceled.")
                return
            if settings.retriever.tag_content(tenant_id, kb_ids, doc, all_tags, topn_tags=topn_tags, S=S) and len(doc.get(TAG_FLD, [])) > 0:
                examples.append({"content": doc["content_with_weight"], TAG_FLD: doc[TAG_FLD]})
            else:
                docs_to_tag.append(doc)

        async def doc_content_tagging(chat_mdl, d, topn_tags):
            cached = get_llm_cache(chat_mdl.llm_name, d["content_with_weight"], all_tags, {"topn": topn_tags})
            if not cached:
                if ctx.has_canceled_func(ctx.id):
                    ctx.progress_cb(-1, msg="Task has been canceled.")
                    return
                picked_examples = random.choices(examples, k=2) if len(examples) > 2 else examples
                if not picked_examples:
                    picked_examples.append({"content": "This is an example", TAG_FLD: {"example": 1}})
                async with chat_limiter:
                    cached = await content_tagging(
                        chat_mdl,
                        d["content_with_weight"],
                        all_tags,
                        picked_examples,
                        topn_tags,
                    )
                if cached:
                    cached = json.dumps(cached)
            if cached:
                set_llm_cache(chat_mdl.llm_name, d["content_with_weight"], cached, all_tags, {"topn": topn_tags})
                d[TAG_FLD] = json.loads(cached)

        tasks = []
        for doc in docs_to_tag:
            tasks.append(asyncio.create_task(doc_content_tagging(chat_model, doc, topn_tags)))
        try:
            await asyncio.gather(*tasks, return_exceptions=False)
        except Exception as e:
            logging.error(f"Error tagging docs: {e}")
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        ctx.progress_cb(msg=f"Tagging {len(docs)} chunks completed in {timer() - st:.2f}s")


def count_with_key(docs: list[dict], key: str) -> int:
    """Count docs that have a specific key.

    Args:
        docs: List of chunk dictionaries.
        key: The key to check for.

    Returns:
        Count of docs that have the key.
    """
    return sum(1 for d in docs if d.get(key))


async def run_document_post_chunking_if_last(
    handler,
    embedding_model: LLMBundle,
    vector_size: int,
    task_start_ts: float,
    chunks_len: int,
    token_count: int,
) -> bool:
    """Gate: only the last chunking task for a doc runs post-processing.
    Returns ``True`` if the caller may proceed to its own terminal
    progress update, ``False`` if the task was cancelled.

    """
    ctx = handler._task_context
    task_id = ctx.id
    task_doc_id = ctx.doc_id

    if ctx.has_canceled_func(task_id):
        abort_doc_chunking_counter(task_doc_id, parse_run_id=ctx.parse_run_id)
        ctx.progress_cb(-1, msg="Task has been canceled.")
        return False

    chunking_aborted = is_doc_chunking_aborted(task_doc_id, parse_run_id=ctx.parse_run_id)
    remaining_chunking_tasks = 0 if ctx.write_interceptor else credit_doc_chunking_task(task_doc_id, task_id, parse_run_id=ctx.parse_run_id)
    if remaining_chunking_tasks != 0:
        if chunking_aborted:
            logging.info(
                "Chunking for doc %s was aborted before task %s reached post-processing; skip document finalizers.",
                task_doc_id,
                task_id,
            )
        elif remaining_chunking_tasks is not None and remaining_chunking_tasks < 0:
            logging.warning(
                "Chunking counter for doc %s is missing or expired after task %s; skip post-processing to avoid duplicate finalizers.",
                task_doc_id,
                task_id,
            )
        else:
            logging.info(
                "Chunk doc(%s), page(%s-%s), chunks(%s), token(%s), elapsed:%.2f; waiting for %s chunking task(s) before post-processing",
                ctx.name,
                ctx.from_page,
                ctx.to_page,
                chunks_len,
                token_count,
                timer() - task_start_ts,
                remaining_chunking_tasks,
            )
        return True

    clear_doc_chunking_counter(task_doc_id, parse_run_id=ctx.parse_run_id)

    if ctx.has_canceled_func(task_id):
        abort_doc_chunking_counter(task_doc_id, parse_run_id=ctx.parse_run_id)
        ctx.progress_cb(-1, msg="Task has been canceled.")
        return False
    return True
