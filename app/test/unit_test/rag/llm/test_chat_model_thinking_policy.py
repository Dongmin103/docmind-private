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

import pytest

from rag.llm import SupportedLiteLLMProvider
from rag.llm.chat_model import _apply_model_family_policies

pytestmark = pytest.mark.p1


def test_qwen3_uses_system_disabled_default():
    gen_conf, kwargs = _apply_model_family_policies(
        "qwen3-plus",
        backend="base",
        gen_conf={},
        request_kwargs={},
    )

    assert gen_conf == {}
    assert kwargs["extra_body"]["enable_thinking"] is False


def test_qwen3_can_enable_thinking_explicitly():
    gen_conf, kwargs = _apply_model_family_policies(
        "qwen3-plus",
        backend="base",
        gen_conf={"thinking": "enabled", "temperature": 0.2},
        request_kwargs={"extra_body": {"seed": 1}},
    )

    assert gen_conf == {"temperature": 0.2}
    assert kwargs["extra_body"] == {"seed": 1, "enable_thinking": True}


def test_qwen3_preview_variant_forces_thinking_true():
    """qwen3.x-preview models (e.g. qwen3.8-max-preview) only accept enable_thinking=True."""
    gen_conf, kwargs = _apply_model_family_policies(
        "qwen3.8-max-preview",
        backend="base",
        gen_conf={},
        request_kwargs={},
    )

    assert gen_conf == {}
    assert kwargs["extra_body"]["enable_thinking"] is True


def test_qwen3_preview_ignores_disabled_thinking():
    """Even with thinking=disabled, -preview still forces enable_thinking=True."""
    gen_conf, kwargs = _apply_model_family_policies(
        "qwen3.8-max-preview",
        backend="base",
        gen_conf={"thinking": "disabled", "temperature": 0.2},
        request_kwargs={},
    )

    assert "thinking" not in gen_conf
    assert gen_conf == {"temperature": 0.2}
    assert kwargs["extra_body"]["enable_thinking"] is True


def test_qwen3_24t_a95b_forces_thinking_true():
    """qwen3.8-2.4t-a95b (flagship reasoning model) only accepts enable_thinking=True."""
    gen_conf, kwargs = _apply_model_family_policies(
        "qwen3.8-2.4t-a95b",
        backend="base",
        gen_conf={},
        request_kwargs={},
    )

    assert gen_conf == {}
    assert kwargs["extra_body"]["enable_thinking"] is True


def test_qwen3_24t_a95b_ignores_disabled_thinking():
    """Even with thinking=disabled, qwen3.8-2.4t-a95b still forces enable_thinking=True."""
    gen_conf, kwargs = _apply_model_family_policies(
        "qwen3.8-2.4t-a95b",
        backend="base",
        gen_conf={"thinking": "disabled", "temperature": 0.2},
        request_kwargs={},
    )

    assert "thinking" not in gen_conf
    assert gen_conf == {"temperature": 0.2}
    assert kwargs["extra_body"]["enable_thinking"] is True
