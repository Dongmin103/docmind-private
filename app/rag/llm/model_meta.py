#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
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
import json
from abc import ABC
from json.decoder import JSONDecodeError

import aiohttp

from common.constants import LLMType


class Base(ABC):
    def __init__(self, api_key: str, base_url: str = None):
        self.api_key = api_key
        self.base_url = base_url

    def _get_api_key(self):
        # Credentials may be stored as a JSON key envelope.
        if not self.api_key:
            return ""
        try:
            parsed = json.loads(self.api_key)
        except (JSONDecodeError, TypeError, ValueError):
            return self.api_key
        if isinstance(parsed, dict):
            return parsed.get("api_key", "") if "api_key" in parsed else ""
        return self.api_key

    def _get_model_list_url(self):
        if not self.base_url:
            return None
        if "/v1" in self.base_url:
            return self.base_url.split("/v1")[0].rstrip("/") + "/v1/models"
        return self.base_url.rstrip("/") + "/v1/models"

    async def _get_raw_model_list(self):
        url = self._get_model_list_url()
        if not url:
            return None
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"Authorization": f"Bearer {self._get_api_key()}"}) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()

    def _format_model_list(self, raw_model_list):
        return raw_model_list

    async def get_model_list(self):
        raw_model_list = await self._get_raw_model_list()
        if not raw_model_list:
            return []
        return self._format_model_list(raw_model_list)


class OpenAIAPICompatible(Base):
    _FACTORY_NAME = "OpenAI-API-Compatible"

    _EMBEDDING_HINTS = ("embed", "embedding", "bge", "gte")
    _RERANK_HINTS = ("rerank", "reranker")
    _ASR_HINTS = ("asr", "stt", "transcribe", "transcriber", "whisper")
    _TTS_HINTS = ("tts", "text-to-speech")
    _VISION_HINTS = (
        "vl",
        "vision",
        "llava",
        "internvl",
        "minicpm-v",
        "gpt-4o",
        "glm-4v",
        "qvq",
        "qwen-vl",
        "pixtral",
    )

    @classmethod
    def _contains_hint(cls, model_name, hints):
        return any(hint in model_name for hint in hints)

    @classmethod
    def _infer_model_types(cls, model_name):
        if cls._contains_hint(model_name, cls._RERANK_HINTS):
            return []
        if cls._contains_hint(model_name, cls._EMBEDDING_HINTS):
            return [LLMType.EMBEDDING.value]
        if cls._contains_hint(model_name, cls._ASR_HINTS):
            return []
        if cls._contains_hint(model_name, cls._TTS_HINTS):
            return []

        model_types = [LLMType.CHAT.value]
        if cls._contains_hint(model_name, cls._VISION_HINTS):
            model_types.append(LLMType.VISION.value)
        return model_types

    def _format_model_list(self, raw_model_list):
        models = raw_model_list.get("data") if isinstance(raw_model_list, dict) else raw_model_list
        if not isinstance(models, list):
            return []

        model_list = []
        for model in models:
            if not isinstance(model, dict):
                continue

            model_name = model.get("id") or model.get("name")
            if not model_name:
                continue

            model_name_lower = model_name.lower()
            model_types = self._infer_model_types(model_name_lower)
            if not model_types:
                continue
            model_list.append(
                {
                    "name": model_name,
                    "model_types": model_types,
                    "features": [],
                    "max_tokens": (model.get("max_tokens") or model.get("max_completion_tokens") or model.get("context_length") or model.get("max_model_len") or 8192),
                }
            )

        return model_list
