"""All retained normalized formats use the real OfficeChunker and adapter."""
from types import SimpleNamespace

import pytest
from common.token_utils import num_tokens_from_string
from rag.parser_platform.schemas import (BlockType, DocxProvenance, HwpProvenance, ParsedBlock, ParsedDocument, ParserRunStatus, PdfProvenance, PptxProvenance, SourceFormat, XlsxProvenance)
from rag.svr.task_executor_refactor.chunk_service import chunk_parser_platform_document


def _provenance(source_format, index):
    if source_format == SourceFormat.PDF:
        return PdfProvenance(page=1, bbox=(0, index * 20, 200, index * 20 + 10))
    if source_format in {SourceFormat.XLS, SourceFormat.XLSX}:
        return XlsxProvenance(sheet='Sheet1', cell_range=f'A{index+1}')
    if source_format == SourceFormat.PPTX:
        return PptxProvenance(slide=index+1, shape_locator='shape/1', bbox=(0, 0, 100, 50))
    if source_format in {SourceFormat.HWP, SourceFormat.HWPX}:
        return HwpProvenance(kind=source_format.value, block_locator=f'block/{index}', page=1)
    return DocxProvenance(item_locator=f'block/{index}')


@pytest.mark.asyncio
@pytest.mark.parametrize('source_format', list(SourceFormat))
async def test_all_formats_keep_real_chunk_content_order_budget_and_provenance(source_format):
    texts = ['first synthetic paragraph', 'second synthetic paragraph']
    blocks = tuple(ParsedBlock(stable_block_id=f'{index+1:032x}', source_item_id=f'block/{index}', block_type=BlockType.TEXT, reading_order=index, text=text, provenance=(_provenance(source_format, index),), diagnostics={'kordoc_row_texts': [text], 'kordoc_table': {'rows': 1, 'cols': 1, 'cells': [[{'text': text}]]}} if source_format in {SourceFormat.XLS, SourceFormat.XLSX} else {}) for index, text in enumerate(texts))
    document = ParsedDocument(schema_version='parser-platform-v1', source_document_id='synthetic', source_hash='0'*64, source_format=source_format, parse_run_id='1'*32, chunk_set_id='2'*32, parser_name='kordoc', parser_version='synthetic', backend='kordoc', status=ParserRunStatus.NORMALIZING, blocks=blocks)
    chunks = await chunk_parser_platform_document(document, config=SimpleNamespace(pptx_native_enabled=False), parser_config={'chunk_token_num': 128, 'excel_chunk_token_num': 128, 'delimiter': '\n'}, source_bytes=b'synthetic')
    content = '\n'.join(chunk['content_with_weight'] for chunk in chunks)
    assert content.index('first synthetic paragraph') < content.index('second synthetic paragraph')
    assert all(num_tokens_from_string(chunk['content_with_weight']) <= 128 for chunk in chunks)
    assert all(chunk['metadata']['parser_platform']['parse_run_id'] == document.parse_run_id and chunk['metadata']['parser_platform']['chunk_set_id'] == document.chunk_set_id for chunk in chunks)
    assert all(chunk['metadata']['parser_platform']['contributing_provenance'] for chunk in chunks)
    if source_format == SourceFormat.PDF:
        assert all(chunk.get('position_int') for chunk in chunks)
    else:
        assert all(chunk['metadata']['parser_platform'].get('source_locators') for chunk in chunks)
