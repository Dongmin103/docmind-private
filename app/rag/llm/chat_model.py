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
import asyncio
import json
import logging
import os
import random
import re
import time
from abc import ABC
from copy import deepcopy

import litellm
from openai import AsyncOpenAI, OpenAI
from enum import StrEnum

from common.llm_request_context import current_llm_user
from common.token_utils import num_tokens_from_string, total_token_count_from_response, usage_from_response
from rag.llm import FACTORY_DEFAULT_BASE_URL, LITELLM_PROVIDER_PREFIX, SupportedLiteLLMProvider
from rag.nlp import is_chinese
from rag.utils.url_utils import ensure_v1


class LLMErrorCode(StrEnum):
    ERROR_RATE_LIMIT = "RATE_LIMIT_EXCEEDED"
    ERROR_AUTHENTICATION = "AUTH_ERROR"
    ERROR_INVALID_REQUEST = "INVALID_REQUEST"
    ERROR_SERVER = "SERVER_ERROR"
    ERROR_TIMEOUT = "TIMEOUT"
    ERROR_CONNECTION = "CONNECTION_ERROR"
    ERROR_MODEL = "MODEL_ERROR"
    ERROR_MAX_ROUNDS = "ERROR_MAX_ROUNDS"
    ERROR_CONTENT_FILTER = "CONTENT_FILTERED"
    ERROR_QUOTA = "QUOTA_EXCEEDED"
    ERROR_MAX_RETRIES = "MAX_RETRIES_EXCEEDED"
    ERROR_GENERIC = "GENERIC_ERROR"


ERROR_PREFIX = "**ERROR**"
LENGTH_NOTIFICATION_CN = "······\n由于大模型的上下文窗口大小限制，回答已经被大模型截断。"
LENGTH_NOTIFICATION_EN = "...\nThe answer is truncated by your chosen LLM due to its limitation on context length."

# Generation parameters that are safe to forward to the underlying completion
# call. `gen_conf` originates from a chat assistant's `llm_setting`, which can
# also carry RAGFlow-internal metadata (e.g. `model_type`). Anything outside
# this set is dropped so providers don't reject the request with errors like
# "Extra inputs are not permitted" / "Unknown parameter: 'model_type'" (#15427).
ALLOWED_GEN_CONF_KEYS = frozenset(
    {
        "temperature",
        "max_completion_tokens",
        "top_p",
        "stream",
        "stream_options",
        "stop",
        "n",
        "presence_penalty",
        "frequency_penalty",
        "functions",
        "function_call",
        "logit_bias",
        "user",
        "response_format",
        "seed",
        "tools",
        "tool_choice",
        "logprobs",
        "top_logprobs",
        "extra_headers",
    }
)

# LiteLLM additionally understands reasoning-control parameters that the
# model-family policies may inject into `gen_conf` (e.g. `thinking` for
# Anthropic / Kimi reasoning models, `enable_thinking` for Qwen models,
# `reasoning_effort` for OpenAI o-series).
LITELLM_ALLOWED_GEN_CONF_KEYS = ALLOWED_GEN_CONF_KEYS | frozenset(
    {
        "thinking",
        "enable_thinking",
        "reasoning_effort",
        "extra_body",
    }
)


def _apply_model_family_policies(
    model_name: str,
    *,
    backend: str,
    provider: SupportedLiteLLMProvider | str | None = None,
    gen_conf: dict | None = None,
    request_kwargs: dict | None = None,
):
    model_name_lower = (model_name or "").lower()
    sanitized_gen_conf = deepcopy(gen_conf) if gen_conf else {}
    sanitized_kwargs = dict(request_kwargs) if request_kwargs else {}

    def _thinking_type():
        val = sanitized_gen_conf.get("thinking")
        if isinstance(val, dict):
            val = val.get("type")

        enable_thinking = sanitized_gen_conf.get("enable_thinking")

        if isinstance(val, str) and val in {"enabled", "disabled"}:
            return val
        if isinstance(enable_thinking, bool):
            return "enabled" if enable_thinking else "disabled"
        return None

    def _pop_thinking_controls():
        sanitized_gen_conf.pop("thinking", None)
        sanitized_gen_conf.pop("enable_thinking", None)

    def _merge_extra_body(target: dict, extra: dict) -> None:
        body = target.get("extra_body")
        if not isinstance(body, dict):
            body = {}
        body.update(extra)
        target["extra_body"] = body

    thinking_type = _thinking_type()

    # Qwen3 keeps RAGFlow's system default of disabling thinking unless explicitly overridden.
    if "qwen3" in model_name_lower:
        _pop_thinking_controls()
        # -preview variants (e.g. qwen3.8-max-preview) and the flagship
        # reasoning model qwen3.8-2.4t-a95b only accept
        # enable_thinking=True; the API rejects any other value.
        if "-preview" in model_name_lower or "2.4t-a95b" in model_name_lower:
            enable_thinking = True
        else:
            enable_thinking = thinking_type == "enabled" if thinking_type else False
        _merge_extra_body(sanitized_kwargs, {"enable_thinking": enable_thinking})

    if backend == "base":
        return sanitized_gen_conf, sanitized_kwargs

    if backend == "litellm" and provider == SupportedLiteLLMProvider.OpenAI and "gpt-5" in model_name_lower:
        for key in ("temperature", "top_p", "logprobs", "top_logprobs"):
            sanitized_gen_conf.pop(key, None)
            sanitized_kwargs.pop(key, None)
    return sanitized_gen_conf, sanitized_kwargs


class Base(ABC):
    def __init__(self, key, model_name, base_url, **kwargs):
        timeout = int(os.environ.get("LLM_TIMEOUT_SECONDS", 600))
        self.base_url = ensure_v1(base_url)
        self.client = OpenAI(api_key=key, base_url=self.base_url, timeout=timeout)
        self.async_client = AsyncOpenAI(api_key=key, base_url=self.base_url, timeout=timeout)
        self.model_name = model_name
        # Configure retry parameters
        self.max_retries = kwargs.get("max_retries", int(os.environ.get("LLM_MAX_RETRIES", 5)))
        self.base_delay = kwargs.get("retry_interval", float(os.environ.get("LLM_BASE_DELAY", 2.0)))
        # Token usage split (prompt/completion/total) of the most recent chat call.
        # Consumed by LLMBundle for accurate Langfuse reporting and run aggregation.
        self.last_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _get_delay(self):
        return self.base_delay * random.uniform(10, 150)

    def _classify_error(self, error):
        error_str = str(error).lower()

        keywords_mapping = [
            (["quota", "capacity", "credit", "billing", "balance", "欠费"], LLMErrorCode.ERROR_QUOTA),
            (["rate limit", "429", "tpm limit", "too many requests", "requests per minute"], LLMErrorCode.ERROR_RATE_LIMIT),
            (["auth", "key", "apikey", "401", "forbidden", "permission"], LLMErrorCode.ERROR_AUTHENTICATION),
            (["invalid", "bad request", "400", "format", "malformed", "parameter"], LLMErrorCode.ERROR_INVALID_REQUEST),
            (["server", "503", "502", "504", "500", "unavailable"], LLMErrorCode.ERROR_SERVER),
            (["timeout", "timed out"], LLMErrorCode.ERROR_TIMEOUT),
            (["connect", "network", "unreachable", "dns"], LLMErrorCode.ERROR_CONNECTION),
            (["filter", "content", "policy", "blocked", "safety", "inappropriate"], LLMErrorCode.ERROR_CONTENT_FILTER),
            (["model", "not found", "does not exist", "not available"], LLMErrorCode.ERROR_MODEL),
            (["max rounds"], LLMErrorCode.ERROR_MODEL),
        ]
        for words, code in keywords_mapping:
            if re.search("({})".format("|".join(words)), error_str):
                return code

        return LLMErrorCode.ERROR_GENERIC

    def _clean_conf(self, gen_conf):
        if "max_tokens" in gen_conf:
            del gen_conf["max_tokens"]

        gen_conf = {k: v for k, v in gen_conf.items() if k in ALLOWED_GEN_CONF_KEYS}
        return gen_conf

    async def _async_chat_streamly(self, history, gen_conf, **kwargs):
        logging.info("[HISTORY STREAMLY]" + json.dumps(history, ensure_ascii=False, indent=4))
        reasoning_start = False

        gen_conf, extra_request_kwargs = _apply_model_family_policies(
            self.model_name,
            backend="base",
            gen_conf=gen_conf,
            request_kwargs={},
        )
        request_kwargs = {"model": self.model_name, "messages": history, "stream": True, **gen_conf}
        stop = kwargs.get("stop")
        if stop:
            request_kwargs["stop"] = stop
        request_kwargs.update(extra_request_kwargs)

        response = await self.async_client.chat.completions.create(**request_kwargs)
        async for resp in response:
            if not resp.choices:
                continue
            if not resp.choices[0].delta.content:
                resp.choices[0].delta.content = ""
            _reasoning = getattr(resp.choices[0].delta, "reasoning_content", None) or getattr(resp.choices[0].delta, "reasoning", None)
            if kwargs.get("with_reasoning", True) and _reasoning:
                ans = ""
                if not reasoning_start:
                    reasoning_start = True
                    ans = "<think>"
                ans += _reasoning + "</think>"
            else:
                reasoning_start = False
                ans = resp.choices[0].delta.content
            tol = total_token_count_from_response(resp)
            if not tol:
                tol = num_tokens_from_string(resp.choices[0].delta.content)

            finish_reason = resp.choices[0].finish_reason if hasattr(resp.choices[0], "finish_reason") else ""
            if finish_reason == "length":
                if is_chinese(ans):
                    ans += LENGTH_NOTIFICATION_CN
                else:
                    ans += LENGTH_NOTIFICATION_EN
            yield ans, tol

    async def async_chat_streamly(self, system, history, gen_conf: dict | None = None, **kwargs):
        gen_conf = dict(gen_conf or {})
        if system and history and history[0].get("role") != "system":
            history.insert(0, {"role": "system", "content": system})
        gen_conf = self._clean_conf(gen_conf)
        ans = ""
        total_tokens = 0
        # Reset so a stale split from a previous call can't leak into this one.
        self.last_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        for attempt in range(self.max_retries + 1):
            try:
                async for delta_ans, tol in self._async_chat_streamly(history, gen_conf, **kwargs):
                    ans = delta_ans
                    total_tokens += tol
                    yield ans

                yield total_tokens
                return
            except Exception as e:
                e = await self._exceptions_async(e, attempt)
                if e:
                    yield e
                    yield total_tokens
                    return

    def _length_stop(self, ans):
        if is_chinese([ans]):
            return ans + LENGTH_NOTIFICATION_CN
        return ans + LENGTH_NOTIFICATION_EN

    @property
    def _retryable_errors(self) -> set[str]:
        return {
            LLMErrorCode.ERROR_RATE_LIMIT,
            LLMErrorCode.ERROR_SERVER,
        }

    def _should_retry(self, error_code: str) -> bool:
        return error_code in self._retryable_errors

    def _exceptions(self, e, attempt) -> str | None:
        logging.exception("OpenAI chat_with_tools")
        # Classify the error
        error_code = self._classify_error(e)
        if attempt == self.max_retries:
            error_code = LLMErrorCode.ERROR_MAX_RETRIES

        if self._should_retry(error_code):
            delay = self._get_delay()
            logging.warning(f"Error: {error_code}. Retrying in {delay:.2f} seconds... (Attempt {attempt + 1}/{self.max_retries})")
            time.sleep(delay)
            return None

        msg = f"{ERROR_PREFIX}: {error_code} - {str(e)}"
        logging.error(f"sync base giving up: {msg}")
        return msg

    async def _exceptions_async(self, e, attempt):
        logging.exception("OpenAI async completion")
        error_code = self._classify_error(e)
        if attempt == self.max_retries:
            error_code = LLMErrorCode.ERROR_MAX_RETRIES

        if self._should_retry(error_code):
            delay = self._get_delay()
            logging.warning(f"Error: {error_code}. Retrying in {delay:.2f} seconds... (Attempt {attempt + 1}/{self.max_retries})")
            await asyncio.sleep(delay)
            return None

        msg = f"{ERROR_PREFIX}: {error_code} - {str(e)}"
        logging.error(f"async base giving up: {msg}")
        return msg


    async def _async_chat(self, history, gen_conf, **kwargs):
        logging.info("[HISTORY]" + json.dumps(history, ensure_ascii=False, indent=2))
        if self.model_name.lower().find("qwq") >= 0:
            logging.info(f"[INFO] {self.model_name} detected as reasoning model, using async_chat_streamly")

            final_ans = ""
            tol_token = 0
            async for delta, tol in self._async_chat_streamly(history, gen_conf, with_reasoning=False, **kwargs):
                if delta.startswith("<think>") or delta.endswith("</think>"):
                    continue
                final_ans += delta
                tol_token = tol

            if len(final_ans.strip()) == 0:
                final_ans = "**ERROR**: Empty response from reasoning model"

            return final_ans.strip(), tol_token

        gen_conf, kwargs = _apply_model_family_policies(
            self.model_name,
            backend="base",
            gen_conf=gen_conf,
            request_kwargs=kwargs,
        )

        response = await self.async_client.chat.completions.create(model=self.model_name, messages=history, **gen_conf, **kwargs)

        # Capture prompt/completion split for accurate Langfuse + run aggregation.
        self.last_usage = usage_from_response(response)
        if not response.choices or not response.choices[0].message or not response.choices[0].message.content:
            return "", 0
        ans = response.choices[0].message.content.strip()
        if response.choices[0].finish_reason == "length":
            ans = self._length_stop(ans)
        return ans, total_token_count_from_response(response)

    async def async_chat(self, system, history, gen_conf=None, **kwargs):
        gen_conf = dict(gen_conf or {})
        if system and history and history[0].get("role") != "system":
            history.insert(0, {"role": "system", "content": system})
        gen_conf = self._clean_conf(gen_conf)

        for attempt in range(self.max_retries + 1):
            try:
                return await self._async_chat(history, gen_conf, **kwargs)
            except Exception as e:
                e = await self._exceptions_async(e, attempt)
                if e:
                    return e, 0
        assert False, "Shouldn't be here."


class OpenAI_APIChat(Base):
    _FACTORY_NAME = "OpenAI-API-Compatible"

    def __init__(self, key, model_name, base_url, **kwargs):
        if not base_url:
            raise ValueError("url cannot be None")
        model_name = model_name.split("___")[0]
        super().__init__(key, model_name, base_url, **kwargs)


# MiniMax models sometimes emit their bracket-delimited control/boundary tokens
# into `content` instead of as structured control — e.g. "]<]minimax[>[" — most
# often on tool-calling turns. The token is streamed split across many deltas,
# so it can't be removed per-delta; it must be filtered over a window that spans
# chunk boundaries. This pattern only matches the vendor name when it is wrapped
# in bracket noise on BOTH sides, so ordinary prose that mentions "MiniMax" is
# left untouched. Extend the alternation as further control tokens are observed.


class LiteLLMBase(ABC):
    _FACTORY_NAME = "OpenAI"

    def __init__(self, key, model_name, base_url=None, **kwargs):
        self.timeout = int(os.environ.get("LLM_TIMEOUT_SECONDS", 600))
        self.provider = kwargs.get("provider", "OpenAI")
        if self.provider != "OpenAI":
            raise ValueError(f"Unsupported provider: {self.provider}")
        self.prefix = LITELLM_PROVIDER_PREFIX.get(self.provider, "")
        self.model_name = f"{self.prefix}{model_name}"
        self.api_key = key
        self.base_url = (base_url or FACTORY_DEFAULT_BASE_URL.get(self.provider, "")).rstrip("/")
        # Configure retry parameters
        self.max_retries = kwargs.get("max_retries", int(os.environ.get("LLM_MAX_RETRIES", 5)))
        self.base_delay = kwargs.get("retry_interval", float(os.environ.get("LLM_BASE_DELAY", 2.0)))
        # Token usage split (prompt/completion/total) of the most recent chat call.
        # Consumed by LLMBundle for accurate Langfuse reporting and run aggregation.
        self.last_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _get_delay(self):
        return self.base_delay * random.uniform(10, 150)

    def _classify_error(self, error):
        error_str = str(error).lower()

        keywords_mapping = [
            (["quota", "capacity", "credit", "billing", "balance", "欠费"], LLMErrorCode.ERROR_QUOTA),
            (["rate limit", "429", "tpm limit", "too many requests", "requests per minute"], LLMErrorCode.ERROR_RATE_LIMIT),
            (["auth", "key", "apikey", "401", "forbidden", "permission"], LLMErrorCode.ERROR_AUTHENTICATION),
            (["invalid", "bad request", "400", "format", "malformed", "parameter"], LLMErrorCode.ERROR_INVALID_REQUEST),
            (["server", "503", "502", "504", "500", "unavailable"], LLMErrorCode.ERROR_SERVER),
            (["timeout", "timed out"], LLMErrorCode.ERROR_TIMEOUT),
            (["connect", "network", "unreachable", "dns"], LLMErrorCode.ERROR_CONNECTION),
            (["filter", "content", "policy", "blocked", "safety", "inappropriate"], LLMErrorCode.ERROR_CONTENT_FILTER),
            (["model", "not found", "does not exist", "not available"], LLMErrorCode.ERROR_MODEL),
            (["max rounds"], LLMErrorCode.ERROR_MODEL),
        ]
        for words, code in keywords_mapping:
            if re.search("({})".format("|".join(words)), error_str):
                return code

        return LLMErrorCode.ERROR_GENERIC

    def _clean_conf(self, gen_conf):
        gen_conf, _ = _apply_model_family_policies(
            self.model_name,
            backend="litellm",
            provider=self.provider,
            gen_conf=gen_conf,
        )

        gen_conf.pop("max_tokens", None)
        return {k: v for k, v in gen_conf.items() if k in LITELLM_ALLOWED_GEN_CONF_KEYS}

    def _need_reasoning_content_back(self) -> bool:
        return False

    def _content_stream_sanitizer(self):
        return None

    def _sanitize_answer(self, text: str) -> str:
        return text

    async def async_chat(self, system, history, gen_conf, **kwargs):
        hist = list(history) if history else []
        if system:
            if not hist or hist[0].get("role") != "system":
                hist.insert(0, {"role": "system", "content": system})

        logging.info("[HISTORY]" + json.dumps(hist, ensure_ascii=False, indent=2))
        gen_conf = self._clean_conf(gen_conf)
        _, kwargs = _apply_model_family_policies(
            self.model_name,
            backend="litellm",
            provider=self.provider,
            request_kwargs=kwargs,
        )

        completion_args = self._construct_completion_args(history=hist, stream=False, **{**gen_conf, **kwargs})

        for attempt in range(self.max_retries + 1):
            try:
                response = await litellm.acompletion(
                    **completion_args,
                    drop_params=True,
                    timeout=self.timeout,
                )

                # Capture the prompt/completion split for accurate per-call usage
                # reporting (Langfuse + agent run aggregation).
                self.last_usage = usage_from_response(response)
                if not response.choices or not response.choices[0].message or not response.choices[0].message.content:
                    return "", 0
                ans = response.choices[0].message.content.strip()
                if response.choices[0].finish_reason == "length":
                    ans = self._length_stop(ans)

                return ans, total_token_count_from_response(response)
            except Exception as e:
                e = await self._exceptions_async(e, attempt)
                if e:
                    return e, 0

        assert False, "Shouldn't be here."

    async def async_chat_streamly(self, system, history, gen_conf, **kwargs):
        if system and history and history[0].get("role") != "system":
            history.insert(0, {"role": "system", "content": system})
        logging.info("[HISTORY STREAMLY]" + json.dumps(history, ensure_ascii=False, indent=4))
        gen_conf = self._clean_conf(gen_conf)
        reasoning_start = False
        total_tokens = 0
        # Reset so a stale split from a previous call can't leak into this one.
        self.last_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        completion_args = self._construct_completion_args(history=history, stream=True, **gen_conf)
        stop = kwargs.get("stop")
        if stop:
            completion_args["stop"] = stop
        # Ask the provider to include authoritative usage in the final streaming chunk.
        # drop_params=True ensures this is silently ignored by providers that don't support it.
        completion_args.setdefault("stream_options", {})["include_usage"] = True

        for attempt in range(self.max_retries + 1):
            try:
                stream = await litellm.acompletion(
                    **completion_args,
                    drop_params=True,
                    timeout=self.timeout,
                )

                async for resp in stream:
                    # Authoritative usage may arrive on a usage-only final chunk that
                    # carries no choices (OpenAI/OpenRouter with include_usage). Read it
                    # before the choices guard so the prompt/completion split is captured.
                    _usage = usage_from_response(resp)
                    if _usage["total_tokens"]:
                        total_tokens = _usage["total_tokens"]
                        self.last_usage = _usage

                    if not hasattr(resp, "choices") or not resp.choices:
                        continue

                    delta = resp.choices[0].delta
                    if not hasattr(delta, "content") or delta.content is None:
                        delta.content = ""

                    _reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                    if kwargs.get("with_reasoning", True) and _reasoning:
                        ans = ""
                        if not reasoning_start:
                            reasoning_start = True
                            ans = "<think>"
                        ans += _reasoning + "</think>"
                    else:
                        reasoning_start = False
                        ans = delta.content

                    if not _usage["total_tokens"]:
                        # No authoritative usage yet: keep a running estimate as fallback.
                        total_tokens += num_tokens_from_string(delta.content)

                    finish_reason = resp.choices[0].finish_reason if hasattr(resp.choices[0], "finish_reason") else ""
                    if finish_reason == "length":
                        if is_chinese(ans):
                            ans += LENGTH_NOTIFICATION_CN
                        else:
                            ans += LENGTH_NOTIFICATION_EN

                    yield ans
                yield total_tokens
                return
            except Exception as e:
                e = await self._exceptions_async(e, attempt)
                if e:
                    yield e
                    yield total_tokens
                    return

    def _length_stop(self, ans):
        if is_chinese([ans]):
            return ans + LENGTH_NOTIFICATION_CN
        return ans + LENGTH_NOTIFICATION_EN

    @property
    def _retryable_errors(self) -> set[str]:
        return {
            LLMErrorCode.ERROR_RATE_LIMIT,
            LLMErrorCode.ERROR_SERVER,
        }

    def _should_retry(self, error_code: str) -> bool:
        return error_code in self._retryable_errors

    async def _exceptions_async(self, e, attempt):
        logging.exception("LiteLLMBase async completion")
        error_code = self._classify_error(e)
        if attempt == self.max_retries:
            error_code = LLMErrorCode.ERROR_MAX_RETRIES

        if self._should_retry(error_code):
            delay = self._get_delay()
            logging.warning(f"Error: {error_code}. Retrying in {delay:.2f} seconds... (Attempt {attempt + 1}/{self.max_retries})")
            await asyncio.sleep(delay)
            return None
        error_detail = str(e)
        msg = f"{ERROR_PREFIX}: {error_code} - {error_detail}"
        logging.error(f"async_chat_streamly giving up: {msg}")
        return msg





    def _construct_completion_args(self, history, stream: bool, **kwargs):
        completion_args = {"model": self.model_name, "messages": history, "api_key": self.api_key, **kwargs}
        completion_args.setdefault("num_retries", self.max_retries)
        if "user" not in completion_args:
            request_user = current_llm_user()
            if request_user:
                completion_args["user"] = request_user
        if stream:
            completion_args["stream"] = True
        completion_args["api_base"] = self.base_url
        return completion_args
