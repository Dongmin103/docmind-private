"""Opt-in reviewed answer path. Staged source; not activated or deployed."""

import asyncio
import json
import time
from urllib.parse import urlsplit

import httpx

from api.apps.services import docmind_answer_review as review
from api.apps.services import docmind_answer_service as answer_service
from api.apps.services import docmind_api_service as search_service

MODEL_DEADLINE_SECONDS = 600


def _local_root(base_url):
    parsed = urlsplit(base_url)
    if parsed.path != "/v1" or parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.port:
        raise answer_service.AnswerError("DOCMIND_ANSWER_CONFIG_INVALID")
    return base_url[:-3]


class _LocalTokenCounter:
    """Exact measured apply-template/tokenize contract; no prompt or source logging."""

    def __init__(self, base_url, output_tokens):
        self.root = _local_root(base_url)
        self.output_tokens = output_tokens
        self.client = httpx.Client(timeout=httpx.Timeout(20, connect=5), trust_env=False, follow_redirects=False)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.client.close()

    def _post(self, path, payload):
        response = self.client.post(self.root + path, json=payload)
        response.raise_for_status()
        return response.json()

    def input_budget(self):
        response = self.client.get(self.root + "/slots")
        response.raise_for_status()
        slots = response.json()
        if not isinstance(slots, list) or not slots:
            raise ValueError("invalid slots")
        limits = [slot.get("n_ctx") for slot in slots if isinstance(slot, dict)]
        if len(limits) != len(slots) or any(type(value) is not int or value <= 0 for value in limits):
            raise ValueError("invalid slot limit")
        budget = min(32768, min(limits) - self.output_tokens - 1024)
        if budget <= 0:
            raise ValueError("invalid input budget")
        return budget

    def count(self, messages, *, actual_wire=False):
        template_kwargs = ({"enable_thinking": True, "reasoning_effort": "low"}
                           if actual_wire else {"enable_thinking": False})
        template = self._post("/apply-template", {"messages": messages, "add_generation_prompt": True,
                                                  "chat_template_kwargs": template_kwargs})
        if not isinstance(template, dict) or not isinstance(template.get("prompt"), str):
            raise TypeError("invalid template")
        tokenized = self._post("/tokenize", {"content": template["prompt"], "add_special": True, "with_pieces": False})
        if not isinstance(tokenized, dict) or not isinstance(tokenized.get("tokens"), list):
            raise TypeError("invalid tokenization")
        return len(tokenized["tokens"])


def _prepare_context(config, question, seeds, expanded, assets):
    try:
        with _LocalTokenCounter(config.base_url, config.output_tokens) as counter:
            budget = counter.input_budget()
            packed, _, _ = review.pack_candidate(question, seeds, expanded, budget, counter.count, assets.common_policy)
            generation = review.generation_messages(assets.common_policy, question, packed,
                                                    assets.generation_task, assets.answer_contract)
            generation = review.with_scope_example(generation, assets.scope_example)
            if counter.count(generation, actual_wire=True) > budget:
                raise review.ContextTooLarge("DOCMIND_CONTEXT_TOO_LARGE")
            return packed, generation, budget
    except review.ContextTooLarge:
        raise answer_service.AnswerError("DOCMIND_CONTEXT_TOO_LARGE") from None
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError):
        raise answer_service.AnswerError("DOCMIND_ANSWER_CONFIG_INVALID") from None


def _check_review_tokens(config, messages, budget):
    try:
        with _LocalTokenCounter(config.base_url, config.output_tokens) as counter:
            if counter.count(messages, actual_wire=True) > budget:
                raise review.ContextTooLarge("DOCMIND_CONTEXT_TOO_LARGE")
    except review.ContextTooLarge:
        raise answer_service.AnswerError("DOCMIND_CONTEXT_TOO_LARGE") from None
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        raise answer_service.AnswerError("DOCMIND_ANSWER_CONFIG_INVALID") from None


async def _review_once(config, messages, draft, citation_count):
    payload = {
        "model": config.model, "messages": messages, "stream": False, "max_tokens": config.output_tokens,
        "temperature": 0, "cache_prompt": True, "reasoning_effort": "low",
        "reasoning_budget_tokens": 256, "reasoning_format": "deepseek",
        "chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "low"},
        "response_format": review.make_review_schema(draft, citation_count),
    }
    try:
        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(MODEL_DEADLINE_SECONDS, connect=5), trust_env=False, follow_redirects=False) as client,
            client.stream("POST", f"{config.base_url}/chat/completions", json=payload, follow_redirects=False) as response,
        ):
                if response.status_code in {400, 422}:
                    raise answer_service.AnswerError("DOCMIND_ANSWER_CONFIG_INVALID")
                if response.status_code in {401, 402, 429}:
                    code = {401: "DOCMIND_LLM_AUTH_FAILED", 402: "DOCMIND_LLM_CREDITS_REQUIRED", 429: "DOCMIND_LLM_RATE_LIMITED"}[response.status_code]
                    raise answer_service.AnswerError(code)
                response.raise_for_status()
                body = bytearray()
                async for block in response.aiter_bytes():
                    body.extend(block)
                    if len(body) > 1_048_576:
                        raise answer_service.AnswerError("DOCMIND_LLM_RESPONSE_INVALID")
        envelope = json.loads(body)
        if not isinstance(envelope, dict) or envelope.get("error") or not isinstance(envelope.get("choices"), list) or len(envelope["choices"]) != 1:
            raise answer_service.AnswerError("DOCMIND_LLM_RESPONSE_INVALID")
        choice = envelope["choices"][0]
        if not isinstance(choice, dict):
            raise answer_service.AnswerError("DOCMIND_LLM_RESPONSE_INVALID")
        if choice.get("finish_reason") == "length":
            raise answer_service.AnswerError("DOCMIND_LLM_OUTPUT_TRUNCATED")
        message = choice.get("message")
        if choice.get("finish_reason") != "stop" or not isinstance(message, dict) or message.get("tool_calls"):
            raise answer_service.AnswerError("DOCMIND_LLM_RESPONSE_INVALID")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise answer_service.AnswerError("DOCMIND_LLM_RESPONSE_INVALID")
        action = json.loads(content, object_pairs_hook=answer_service._unique_object,
                            parse_constant=answer_service._invalid_json_constant)
        final = review.normalize_review_result(draft, action, citation_count)
        validated = answer_service._validate_generated_answer(json.dumps(final, ensure_ascii=False), citation_count)
        return validated
    except answer_service.AnswerError:
        raise
    except httpx.ConnectTimeout:
        raise answer_service.AnswerError("DOCMIND_LLM_UNAVAILABLE") from None
    except (httpx.TimeoutException, TimeoutError):
        raise answer_service.AnswerError("DOCMIND_LLM_TIMEOUT") from None
    except httpx.HTTPError:
        raise answer_service.AnswerError("DOCMIND_LLM_UNAVAILABLE") from None
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
        raise answer_service.AnswerError("DOCMIND_LLM_RESPONSE_INVALID") from None


async def reviewed_answer(tenant_id, question, *, project_id, scope, neighbor_window, config):
    if config.provider != "openai" or config.pipeline != "reviewed_v1" or config.output_tokens not in {2048, 4096}:
        raise answer_service.AnswerError("DOCMIND_ANSWER_CONFIG_INVALID")
    start = time.monotonic()
    try:
        assets = review.load_assets()
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        raise answer_service.AnswerError("DOCMIND_ANSWER_CONFIG_INVALID") from None
    search_start = time.monotonic()
    result = await search_service.search(tenant_id, question, project_id=project_id, scope=scope, answer_mode=True)
    search_ms = round((time.monotonic() - search_start) * 1000)
    if not result["chunks"]:
        return {"answer": answer_service._INSUFFICIENT_NOTICE, "answer_status": "insufficient", "citations": [],
                "dataset_id": result["dataset_id"],
                "context": {"seed_count": 0, "expanded_count": 0, "used_count": 0, "neighbor_window": neighbor_window},
                "elapsed_ms": round((time.monotonic() - start) * 1000),
                "timings_ms": {"search": search_ms, "context": 0, "pack": 0, "generation": 0, "review": 0, "verify": 0}}
    context_start = time.monotonic()
    try:
        seeds, expanded = await asyncio.wait_for(asyncio.to_thread(
            answer_service._load_context, search_service, tenant_id, result, scope, neighbor_window, 16), 20)
    except TimeoutError:
        raise answer_service.AnswerError("DOCMIND_CONTEXT_TIMEOUT") from None
    context_ms = round((time.monotonic() - context_start) * 1000)
    pack_start = time.monotonic()
    packed, generation_messages, budget = await asyncio.to_thread(_prepare_context, config, question, seeds, expanded, assets)
    pack_ms = round((time.monotonic() - pack_start) * 1000)
    verify_ms = generation_ms = review_ms = 0

    async def verify_context():
        nonlocal verify_ms
        mark = time.monotonic()
        try:
            await asyncio.wait_for(asyncio.to_thread(answer_service._verify_context,
                                                     search_service, tenant_id, result, scope, packed), 20)
        except TimeoutError:
            raise answer_service.AnswerError("DOCMIND_CONTEXT_TIMEOUT") from None
        verify_ms += round((time.monotonic() - mark) * 1000)

    await verify_context()
    deadline = time.monotonic() + MODEL_DEADLINE_SECONDS  # Generation + review share one deadline.

    async def within_deadline(operation):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise answer_service.AnswerError("DOCMIND_LLM_TIMEOUT")
        try:
            return await asyncio.wait_for(operation(), remaining)
        except TimeoutError:
            raise answer_service.AnswerError("DOCMIND_LLM_TIMEOUT") from None

    async def generate_once():
        nonlocal generation_ms
        mark = time.monotonic()
        try:
            try:
                generated = await within_deadline(lambda: answer_service.generate(config, generation_messages, len(packed), timeout_seconds=MODEL_DEADLINE_SECONDS))
            except answer_service.AnswerError as error:
                if str(error) in {"DOCMIND_LLM_RESPONSE_INVALID", "DOCMIND_LLM_CITATIONS_INVALID", "DOCMIND_LLM_OUTPUT_TRUNCATED"}:
                    raise review.RecoverableOutput(str(error)) from None
                raise
            return {"status": generated.status, "answer": generated.answer, "citations": list(generated.references)}
        finally:
            generation_ms += round((time.monotonic() - mark) * 1000)

    async def review_once(draft):
        nonlocal review_ms
        mark = time.monotonic()
        try:
            messages = review.external_review_messages([*generation_messages, *assets.calibration], draft,
                                                        question, assets.review_policy)
            await asyncio.to_thread(_check_review_tokens, config, messages, budget)
            try:
                return await within_deadline(lambda: _review_once(config, messages, draft, len(packed)))
            except answer_service.AnswerError as error:
                if str(error) in {"DOCMIND_LLM_RESPONSE_INVALID", "DOCMIND_LLM_CITATIONS_INVALID", "DOCMIND_LLM_OUTPUT_TRUNCATED"}:
                    raise review.RecoverableOutput(str(error)) from None
                raise
        finally:
            review_ms += round((time.monotonic() - mark) * 1000)

    try:
        generated = await review.bounded_generate_review(generate_once, review_once, verify_context)
    except review.RecoverableOutput as error:
        raise answer_service.AnswerError(str(error)) from None
    references = set(generated.references)
    citations = [{
        "number": i + 1, "chunk_id": row["chunk_id"], "doc_id": row["doc_id"],
        "document_name": row.get("docnm_kwd") or row["doc_id"],
        "document_relative_path": row.get("document_relative_path"),
        "source_version_id": row.get("source_version_id"), "chunk_set_id": row.get("chunk_set_id"),
        "page_num_int": row.get("page_num_int"), "content": row["content_with_weight"],
    } for i, row in enumerate(packed) if i + 1 in references]
    return {
        "answer": generated.answer, "answer_status": generated.status, "citations": citations,
        "dataset_id": result["dataset_id"], "trace_id": result["trace_id"], "model": config.model,
        "context": {"seed_count": len(seeds), "expanded_count": len(expanded), "used_count": len(packed),
                    "neighbor_window": neighbor_window, "budget_pruned": len(packed) < len(expanded),
                    "seed_only_count": sum(not answer_service._expandable(seed) for seed in seeds)},
        "elapsed_ms": round((time.monotonic() - start) * 1000),
        "timings_ms": {"search": search_ms, "context": context_ms, "pack": pack_ms,
                       "generation": generation_ms, "review": review_ms, "verify": verify_ms},
    }
