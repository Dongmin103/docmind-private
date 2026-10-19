import inspect
import json
import logging

from common.doc_store.doc_store_base import MatchTextExpr, OrderByExpr
from rag.nlp.docmind_lexical import supplement_expression
from rag.utils.es_conn import ESConnection


def test_lexical_alternatives_remain_inside_the_authorized_scope():
    # Exercise the real ES query builder without constructing a client or DB.
    cls = inspect.getclosurevars(ESConnection).nonlocals["cls"]
    store = object.__new__(cls)
    store.logger = logging.getLogger(__name__)
    captured = {}

    def capture(_indices, body, **_options):
        captured.update(body)
        return {"hits": {"total": {"value": 0}, "hits": []}}

    store._es_search_once = capture
    expression = supplement_expression(
        MatchTextExpr(["content_ltks"], "original", 128), "C# 설치 방법"
    )
    store.search(
        ["doc_id"], [], {"doc_id": ["allowed-document"], "available_int": 1},
        [expression], OrderByExpr(), 0, 128, "fixture-index", ["allowed-dataset"],
    )
    query = captured["query"]["bool"]
    assert {"terms": {"doc_id": ["allowed-document"]}} in query["filter"]
    assert {"terms": {"kb_id": ["allowed-dataset"]}} in query["filter"]
    assert len(query["must"]) == 1
    alternatives = query["must"][0]["dis_max"]["queries"]
    assert len(alternatives) == 3
    assert "query_string" in alternatives[0]
    assert all("multi_match" in row for row in alternatives[1:])
    assert captured["size"] == 128
    assert "allowed-document" not in json.dumps(alternatives)
