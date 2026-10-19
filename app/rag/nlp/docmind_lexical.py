"""Language-independent exact terms and Korean spacing-tolerant BM25 fields.

These fields supplement the existing lexical lane. They never change stored
evidence, embeddings, retrieval scope, candidate limits, or reranker input.
"""

import hashlib
import html
import re
import unicodedata

from common.doc_store.doc_store_base import MatchTextExpr


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKC", html.unescape(re.sub(r"<[^>]+>", " ", text))).casefold()


def _exact_terms(text: str) -> list[str]:
    # Hex encoding preserves punctuation without invoking a query-string parser
    # or a language stemmer (e.g. C++, C#, MES, and model/version identifiers).
    words = re.findall(r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:\+{1,2}|#)?", _plain(text))
    return list(dict.fromkeys("x" + word.encode("utf-8").hex() if len(word) <= 128 else "xh" + hashlib.sha256(word.encode("utf-8")).hexdigest() for word in words))


def _korean_terms(text: str) -> list[str]:
    terms = []
    for span in re.findall(r"[가-힣]+(?:\s+[가-힣]+)*", _plain(text)):
        compact = re.sub(r"\s+", "", span)
        for size in (2, 3):
            terms.extend(compact[i : i + size] for i in range(len(compact) - size + 1))
    return list(dict.fromkeys(terms))


def supplemental_fields(document_name: str, content: str) -> dict[str, str]:
    # Strip only the final filename extension; dots inside model names survive.
    title = re.sub(r"\.[a-zA-Z0-9]{1,8}$", "", document_name)
    return {
        "lexical_title_ko_ltks": " ".join(_korean_terms(title)),
        "lexical_content_ko_ltks": " ".join(_korean_terms(content)),
        "lexical_title_exact_ltks": " ".join(_exact_terms(title)),
        "lexical_content_exact_ltks": " ".join(_exact_terms(content)),
    }


def supplement_expression(expression: MatchTextExpr | None, question: str) -> MatchTextExpr | None:
    alternatives = []
    exact = _exact_terms(question)[:64]
    korean = _korean_terms(question)[:128]
    if exact:
        alternatives.append(
            {
                "multi_match": {
                    "query": " ".join(exact),
                    "fields": ["lexical_title_exact_ltks^10", "lexical_content_exact_ltks^2"],
                    "type": "best_fields",
                    "operator": "and",
                }
            }
        )
    if korean:
        alternatives.append(
            {
                "multi_match": {
                    "query": " ".join(korean),
                    "fields": ["lexical_title_ko_ltks^3", "lexical_content_ko_ltks^0.5"],
                    "type": "best_fields",
                    "minimum_should_match": "70%",
                }
            }
        )
    if not alternatives:
        return expression
    options = dict(expression.extra_options or {}) if expression else {}
    options["lexical_alternatives"] = alternatives
    return MatchTextExpr(expression.fields if expression else ["content_ltks"], expression.matching_text if expression else "", 128, options)
