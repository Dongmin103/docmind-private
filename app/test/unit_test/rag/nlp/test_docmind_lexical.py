from common.doc_store.doc_store_base import MatchTextExpr
from rag.nlp.docmind_lexical import _exact_terms, _korean_terms, supplement_expression, supplemental_fields


def test_korean_spacing_particles_and_unicode_share_search_terms():
    assert _korean_terms("스마트 팩토리") == _korean_terms("스마트팩토리")
    assert set(_korean_terms("설치")) <= set(_korean_terms("설치를 진행합니다"))
    assert _korean_terms("설치") == _korean_terms("설치")
    assert not set(_korean_terms("재고 관리")) & set(_korean_terms("휴가 신청"))


def test_exact_terms_preserve_symbols_versions_and_unstemmed_words():
    assert _exact_terms("C++") != _exact_terms("C#")
    assert _exact_terms("MES") != _exact_terms("ME")
    assert _exact_terms("ZX-3000") != _exact_terms("ZX-3001")
    assert _exact_terms("MODEL_v2.1") != _exact_terms("MODEL_v2.2")
    assert _exact_terms("Ｃ＋＋") == _exact_terms("c++")
    assert _exact_terms("SELECT:*) OR password:*") == _exact_terms("SELECT OR password")
    assert len(_exact_terms("a" * 40000)[0]) < 128
    assert _exact_terms("a" * 40000) != _exact_terms("b" * 40000)


def test_html_markup_is_not_indexed_as_product_terms():
    fields = supplemental_fields("ZX-3000.pdf", "<table><tr><td>C# 설치</td></tr></table>")
    assert fields["lexical_title_exact_ltks"] == " ".join(_exact_terms("ZX-3000"))
    assert fields["lexical_content_exact_ltks"] == " ".join(_exact_terms("C# 설치"))
    assert fields["lexical_content_ko_ltks"] == "설치"


def test_original_expression_is_preserved_and_alternatives_are_bounded():
    base = MatchTextExpr(["content_ltks^2"], "legacy", 128, {"minimum_should_match": 0.3})
    enriched = supplement_expression(base, "MES 스마트 팩토리")
    assert enriched.matching_text == base.matching_text
    assert enriched.fields == base.fields
    assert base.extra_options == {"minimum_should_match": 0.3}
    assert enriched.extra_options["minimum_should_match"] == 0.3
    assert len(enriched.extra_options["lexical_alternatives"]) == 2
    bounded = supplement_expression(base, " ".join(f"x{i}" for i in range(1000)))
    assert len(bounded.extra_options["lexical_alternatives"][0]["multi_match"]["query"].split()) == 64


def test_empty_and_punctuation_queries_never_become_filter_only_searches():
    assert supplement_expression(None, "... !!!") is None
    exact_only = supplement_expression(None, "C++")
    assert exact_only.extra_options["lexical_alternatives"]


def test_index_fields_do_not_modify_evidence_or_add_a_product_dictionary():
    fields = supplemental_fields("UNKNOWN-Q987.pdf", "설치를 진행합니다.")
    assert set(fields) == {"lexical_title_ko_ltks", "lexical_content_ko_ltks", "lexical_title_exact_ltks", "lexical_content_exact_ltks"}
    assert fields["lexical_title_exact_ltks"] == " ".join(_exact_terms("unknown-q987"))
