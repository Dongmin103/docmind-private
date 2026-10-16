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

import re
from typing import Any

_RETIRED_SELECTORS = frozenset({"mineru", "paddleocr", "opendataloader", "somark", "mistral ocr", "docling", "tcadp parser"})


def normalize_layout_recognizer(layout_recognizer_raw: Any) -> tuple[Any, None]:
    if isinstance(layout_recognizer_raw, str):
        name = layout_recognizer_raw.strip().lower()
        if name in _RETIRED_SELECTORS or name.rsplit("@", 1)[-1] in _RETIRED_SELECTORS:
            raise ValueError(f"Unsupported parser selection: {layout_recognizer_raw}")
    return layout_recognizer_raw, None


def validate_parser_selection(parser_id, parser_config=None):
    if str(parser_id).lower() == "audio":
        raise ValueError("Unsupported parser selection: audio")
    config = parser_config or {}
    normalize_layout_recognizer(config.get("layout_recognize"))
    if any(str(key).startswith(("mineru_", "paddleocr_", "opendataloader_", "somark_", "mistral_ocr_")) for key in config):
        raise ValueError("Unsupported retired parser options")


def validate_task_parser_identity(context):
    validate_parser_selection(context.parser_id, context.parser_config)
    run_id = context.parse_run_id
    chunk_id = context.chunk_set_id
    docmind_job = hasattr(context, "_docmind_parser_platform_config") or getattr(context, "_docmind_defer_activation", False)
    if bool(run_id) != bool(chunk_id):
        raise ValueError("Invalid DocMind parser run identity")
    if docmind_job and any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value) for value in (run_id, chunk_id)):
        raise ValueError("Invalid DocMind parser run identity")
