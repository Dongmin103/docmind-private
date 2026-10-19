from __future__ import annotations

from typing import Any


def prepare_parser_platform_standard_chunks(
    chunks: list[dict[str, Any]],
    *,
    document_name: str,
    language: str,
) -> None:
    import re

    from rag.nlp import rag_tokenizer, tokenize

    rag_tokenizer.tokenizer.set_language(language)
    title = re.sub(r"\.[a-zA-Z0-9]{1,8}$", "", document_name)
    title_tokens = rag_tokenizer.tokenize(title)
    title_fine_tokens = rag_tokenizer.fine_grained_tokenize(title_tokens)

    for chunk in chunks:
        content = chunk.get("content_with_weight", "")
        chunk["docnm_kwd"] = document_name
        chunk["title_tks"] = title_tokens
        chunk["title_sm_tks"] = title_fine_tokens
        tokenize(chunk, content, False, language=language)


prepare_hwp_standard_chunks = prepare_parser_platform_standard_chunks
