from rag.parser_platform.search_fields import prepare_parser_platform_standard_chunks


def test_kordoc_chunks_receive_document_name_and_bm25_tokens() -> None:
    chunks = [{"content_with_weight": "밀폐용기와 기밀용기의 차이"}]

    prepare_parser_platform_standard_chunks(chunks, document_name="대한민국약전.hwpx", language="Korean")

    assert chunks[0]["docnm_kwd"] == "대한민국약전.hwpx"
    assert chunks[0]["content_ltks"]
    assert chunks[0]["content_sm_ltks"]
    assert chunks[0]["title_tks"] == "대한민국약전"
    assert chunks[0]["title_sm_tks"]
    assert chunks[0]["lexical_title_ko_ltks"]


def test_titles_and_supplemental_fields_leave_source_text_and_identity_intact() -> None:
    from rag.utils.chunk_id import make_chunk_id

    content = "<table><tr><td>C++ 설치방법</td></tr></table>"
    chunk = {"content_with_weight": content, "parse_run_id": "run", "chunk_set_id": "set"}
    original_id = make_chunk_id("searchable_chunk", "document", chunk=chunk, chunk_order_int=0)
    prepare_parser_platform_standard_chunks([chunk], document_name="ZX-3000_설치.pdf", language="Korean")
    assert chunk["content_with_weight"] == content
    assert chunk["parse_run_id"] == "run"
    assert chunk["chunk_set_id"] == "set"
    assert chunk["title_tks"]
    assert chunk["lexical_title_exact_ltks"]
    assert "설치" in chunk["lexical_content_ko_ltks"].split()
    assert make_chunk_id("searchable_chunk", "document", chunk=chunk, chunk_order_int=0) == original_id
