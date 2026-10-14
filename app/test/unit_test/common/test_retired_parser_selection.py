import pytest
from common.parser_config_utils import normalize_layout_recognizer

@pytest.mark.parametrize('selection', ['MinerU', 'PaddleOCR', 'OpenDataLoader', 'SoMark', 'Mistral OCR', 'model@instance@MinerU'])
def test_explicit_retired_ocr_selection_fails_before_fallback(selection):
    with pytest.raises(ValueError, match='Unsupported'):
        normalize_layout_recognizer(selection)

@pytest.mark.parametrize('selection', ['', False, 'Plain Text'])
def test_plain_attachment_selection_remains_valid(selection):
    assert normalize_layout_recognizer(selection) == (selection, None)
