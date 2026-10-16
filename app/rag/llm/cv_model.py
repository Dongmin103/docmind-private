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

import base64
import logging
import os
import tempfile
from abc import ABC
from io import BytesIO
from pathlib import Path

from openai import OpenAI, AsyncOpenAI

from common.token_utils import total_token_count_from_response
from rag.nlp import is_english
from rag.prompts.generator import vision_llm_describe_prompt
from rag.utils.url_utils import ensure_v1


def _qwen3_no_think_extra_body(model_name: str) -> dict[str, bool] | None:
    """Build DashScope-compatible options that disable Qwen3.x thinking."""
    if "qwen3." in model_name.lower():
        return {"enable_thinking": False}
    return None


def _remove_sampling_params(model_name: str, gen_conf: dict | None) -> dict:
    """Remove sampling options from Qwen3.x CV requests for now."""
    sanitized_gen_conf = dict(gen_conf or {})
    if "qwen3." in model_name.lower():
        for key in ("temperature", "top_p"):
            sanitized_gen_conf.pop(key, None)
    return sanitized_gen_conf


class Base(ABC):
    def __init__(self, **kwargs):
        # Configure retry parameters
        self.max_retries = kwargs.get("max_retries", int(os.environ.get("LLM_MAX_RETRIES", 5)))
        self.base_delay = kwargs.get("retry_interval", float(os.environ.get("LLM_BASE_DELAY", 2.0)))
        self.max_rounds = kwargs.get("max_rounds", 5)
        self.is_tools = False
        self.tools = []
        self.toolcall_sessions = {}
        self.extra_body = None

    def describe(self, image):
        raise NotImplementedError("Please implement encode method!")

    def describe_with_prompt(self, image, prompt=None):
        raise NotImplementedError("Please implement encode method!")

    def _form_history(self, system, history, images=None):
        hist = []
        if system:
            hist.append({"role": "system", "content": system})
        for h in history:
            if images and h["role"] == "user":
                h["content"] = self._image_prompt(h["content"], images)
                images = []
            hist.append(h)
        return hist

    @staticmethod
    def _blob_to_data_url(blob, mime_type="image/png"):
        if isinstance(blob, str):
            blob = blob.strip()
            if blob.startswith("data:") or blob.startswith("http://") or blob.startswith("https://") or blob.startswith("file://"):
                return blob
            return f"data:{mime_type};base64,{blob}"
        if isinstance(blob, BytesIO):
            blob = blob.getvalue()
        if isinstance(blob, memoryview):
            blob = blob.tobytes()
        if isinstance(blob, bytearray):
            blob = bytes(blob)
        if isinstance(blob, bytes):
            b64 = base64.b64encode(blob).decode("utf-8")
            return f"data:{mime_type};base64,{b64}"
        return None

    def _normalize_image(self, image):
        if isinstance(image, dict):
            inline_data = image.get("inline_data")
            if isinstance(inline_data, dict):
                mime = inline_data.get("mime_type") or "image/png"
                data_url = self._blob_to_data_url(inline_data.get("data"), mime)
                if data_url:
                    return data_url

            image_url = image.get("image_url")
            if isinstance(image_url, dict):
                data_url = self._blob_to_data_url(image_url.get("url"), image.get("mime_type") or "image/png")
                if data_url:
                    return data_url
            if isinstance(image_url, str):
                data_url = self._blob_to_data_url(image_url, image.get("mime_type") or "image/png")
                if data_url:
                    return data_url

            if "url" in image:
                data_url = self._blob_to_data_url(image.get("url"), image.get("mime_type") or "image/png")
                if data_url:
                    return data_url

            mime = image.get("mime_type") or image.get("media_type") or "image/png"
            for key in ("blob", "data"):
                if key in image:
                    data_url = self._blob_to_data_url(image.get(key), mime)
                    if data_url:
                        return data_url

        if isinstance(image, (bytes, bytearray, memoryview, BytesIO)):
            return self.image2base64(image)
        if isinstance(image, str):
            return self._blob_to_data_url(image, "image/png")
        return self.image2base64(image)

    def _image_prompt(self, text, images):
        if not images:
            return text

        if isinstance(images, str) or "bytes" in type(images).__name__:
            images = [images]

        pmpt = [{"type": "text", "text": text}]
        for img in images:
            try:
                pmpt.append({"type": "image_url", "image_url": {"url": self._normalize_image(img)}})
            except Exception:
                logging.warning("[%s] Skip invalid image input in request payload.", self.__class__.__name__)
                continue
        return pmpt

    @staticmethod
    def _extract_text_from_content(content):
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            texts = []
            for blk in content:
                if not isinstance(blk, dict):
                    continue
                if blk.get("type") in {"text", "input_text"} and blk.get("text"):
                    texts.append(str(blk["text"]))
                elif "text" in blk and isinstance(blk.get("text"), (str, int, float)):
                    texts.append(str(blk["text"]))
            return "\n".join(texts).strip()
        return ""

    def _resolve_video_prompt(self, system, history, **kwargs):
        prompt = kwargs.get("video_prompt") or kwargs.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            return prompt.strip()

        for h in reversed(history or []):
            if h.get("role") != "user":
                continue
            txt = self._extract_text_from_content(h.get("content"))
            if txt:
                return txt

        if isinstance(system, str) and system.strip():
            return system.strip()

        return "Please summarize this video in proper sentences."

    def _video_frame_to_image_bytes(self, video_bytes, filename="", frame_ratio=0.5):
        import cv2

        suffix = Path(filename).suffix or ".mp4"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(video_bytes)
            tmp.flush()
            cap = cv2.VideoCapture(tmp.name)
            try:
                frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                if frame_count > 1:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, min(frame_count - 1, max(0, int(frame_count * frame_ratio))))
                ok, frame = cap.read()
                if not ok or frame is None:
                    raise RuntimeError("Failed to extract a frame from video.")
                ok, encoded = cv2.imencode(".jpg", frame)
                if not ok:
                    raise RuntimeError("Failed to encode video frame.")
                return encoded.tobytes()
            finally:
                cap.release()

    def _describe_video_frame(self, video_bytes, filename, prompt):
        frames = [self._video_frame_to_image_bytes(video_bytes, filename, r) for r in (0.1, 0.5, 0.9)]
        prompt = f"The attached images are representative frames sampled from a video in chronological order. Summarize the visible video content based on these frames.\n\n{prompt}"
        res = self.client.chat.completions.create(model=self.model_name, messages=self.vision_llm_prompt(frames, prompt), extra_body=self.extra_body)
        if not res.choices:
            raise ValueError("LLM returned empty response")
        return res.choices[0].message.content.strip(), total_token_count_from_response(res)

    async def async_chat(self, system, history, gen_conf, images=None, video_bytes=None, filename="", **kwargs):
        try:
            if video_bytes:
                prompt = self._resolve_video_prompt(system, history, **kwargs)
                return self._describe_video_frame(video_bytes, filename, prompt)

            response = await self.async_client.chat.completions.create(
                model=self.model_name,
                messages=self._form_history(system, history, images),
                extra_body=self.extra_body,
            )
            return response.choices[0].message.content.strip(), response.usage.total_tokens
        except Exception as e:
            return "**ERROR**: " + str(e), 0

    async def async_chat_streamly(self, system, history, gen_conf, images=None, **kwargs):
        ans = ""
        tk_count = 0
        try:
            response = await self.async_client.chat.completions.create(
                model=self.model_name,
                messages=self._form_history(system, history, images),
                stream=True,
                extra_body=self.extra_body,
            )
            async for resp in response:
                if not resp.choices[0].delta.content:
                    continue
                delta = resp.choices[0].delta.content
                ans = delta
                if resp.choices[0].finish_reason == "length":
                    ans += "...\nFor the content length reason, it stopped, continue?" if is_english([ans]) else "······\n由于长度的原因，回答被截断了，要继续吗？"
                if resp.choices[0].finish_reason == "stop":
                    tk_count += resp.usage.total_tokens
                yield ans
        except Exception as e:
            yield ans + "\n**ERROR**: " + str(e)

        yield tk_count

    @staticmethod
    def image2base64_rawvalue(self, image):
        # Return a base64 string without data URL header
        if isinstance(image, bytes):
            b64 = base64.b64encode(image).decode("utf-8")
            return b64
        if isinstance(image, BytesIO):
            data = image.getvalue()
            b64 = base64.b64encode(data).decode("utf-8")
            return b64
        with BytesIO() as buffered:
            try:
                image.save(buffered, format="JPEG")
            except Exception:
                # reset buffer before saving PNG
                buffered.seek(0)
                buffered.truncate()
                image.save(buffered, format="PNG")
            data = buffered.getvalue()
            b64 = base64.b64encode(data).decode("utf-8")
        return b64

    @staticmethod
    def image2base64(image):
        # Return a data URL with the correct MIME to avoid provider mismatches
        if isinstance(image, bytes):
            # Best-effort magic number sniffing
            mime = "image/png"
            if len(image) >= 2 and image[0] == 0xFF and image[1] == 0xD8:
                mime = "image/jpeg"
            b64 = base64.b64encode(image).decode("utf-8")
            return f"data:{mime};base64,{b64}"
        if isinstance(image, BytesIO):
            data = image.getvalue()
            mime = "image/png"
            if len(data) >= 2 and data[0] == 0xFF and data[1] == 0xD8:
                mime = "image/jpeg"
            b64 = base64.b64encode(data).decode("utf-8")
            return f"data:{mime};base64,{b64}"
        with BytesIO() as buffered:
            fmt = "jpeg"
            try:
                image.save(buffered, format="JPEG")
            except Exception:
                # reset buffer before saving PNG
                buffered.seek(0)
                buffered.truncate()
                image.save(buffered, format="PNG")
                fmt = "png"
            data = buffered.getvalue()
            b64 = base64.b64encode(data).decode("utf-8")
            mime = f"image/{fmt}"
        return f"data:{mime};base64,{b64}"

    def prompt(self, b64):
        return [
            {
                "role": "user",
                "content": self._image_prompt(
                    "请用中文详细描述一下图中的内容，比如时间，地点，人物，事情，人物心情等，如果有数据请提取出数据。"
                    if self.lang.lower() == "chinese"
                    else "Please describe the content of this picture, like where, when, who, what happen. If it has number data, please extract them out.",
                    b64,
                ),
            }
        ]

    def vision_llm_prompt(self, b64, prompt=None):
        return [{"role": "user", "content": self._image_prompt(prompt if prompt else vision_llm_describe_prompt(), b64)}]


class GptV4(Base):
    _FACTORY_NAME = "OpenAI"

    def __init__(self, key, model_name="gpt-4-vision-preview", lang="Chinese", base_url="https://api.openai.com/v1", **kwargs):
        if not base_url:
            base_url = "https://api.openai.com/v1"
        self.base_url = ensure_v1(base_url)
        self.api_key = key
        self.client = OpenAI(api_key=key, base_url=self.base_url)
        self.async_client = AsyncOpenAI(api_key=key, base_url=self.base_url)
        self.model_name = model_name
        self.lang = lang
        super().__init__(**kwargs)

    def describe(self, image):
        b64 = self.image2base64(image)
        res = self.client.chat.completions.create(model=self.model_name, messages=self.prompt(b64), extra_body=self.extra_body)
        if not res.choices:
            raise ValueError("LLM returned empty response")  # pact: guard empty choices list
        return res.choices[0].message.content.strip(), total_token_count_from_response(res)

    def describe_with_prompt(self, image, prompt=None):
        b64 = self.image2base64(image)
        res = self.client.chat.completions.create(
            model=self.model_name,
            messages=self.vision_llm_prompt(b64, prompt),
            extra_body=self.extra_body,
        )
        if not res.choices:
            raise ValueError("LLM returned empty response")  # pact: guard empty choices list
        return res.choices[0].message.content.strip(), total_token_count_from_response(res)


class OpenAI_APICV(GptV4):
    _FACTORY_NAME = "OpenAI-API-Compatible"

    def __init__(self, key, model_name, lang="Chinese", base_url="", **kwargs):
        if not base_url:
            raise ValueError("url cannot be None")
        self.base_url = ensure_v1(base_url)
        self.client = OpenAI(api_key=key, base_url=self.base_url)
        self.async_client = AsyncOpenAI(api_key=key, base_url=self.base_url)
        self.model_name = model_name.split("___")[0]
        self.lang = lang
        Base.__init__(self, **kwargs)
