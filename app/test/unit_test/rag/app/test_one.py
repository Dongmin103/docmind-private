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
from unittest.mock import Mock

from rag.app import one


def test_docx_chunk_preserves_text_order_table_and_language(monkeypatch):
    parser = Mock(return_value=[("first", None, None), ("caption", object(), None), ("table:", None, [["cell"]]), ("last", None, None)])
    monkeypatch.setattr(one.naive, "Docx", Mock(return_value=parser))
    monkeypatch.setattr(one.rag_tokenizer, "tokenize", lambda text: text)
    monkeypatch.setattr(one.rag_tokenizer, "fine_grained_tokenize", lambda text: text)
    tokenize = Mock()
    monkeypatch.setattr(one, "tokenize", tokenize)
    result = one.chunk("document.docx", binary=b"docx", lang="Japanese", callback=lambda *_: None)
    assert len(result) == 1
    parser.assert_called_once_with("document.docx", b"docx")
    assert tokenize.call_args.args[1] == "first\ncaption\ntable:[['cell']]\nlast"
    assert tokenize.call_args.kwargs == {"language": "Japanese"}
