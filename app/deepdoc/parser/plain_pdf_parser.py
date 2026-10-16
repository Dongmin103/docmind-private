"""Plain text PDF attachments without neural layout or provider OCR."""
import logging
from io import BytesIO
from pypdf import PdfReader as pdf2_read
from common.constants import MAXIMUM_PAGE_NUMBER
from deepdoc.parser.utils import extract_pdf_outlines


class PlainParser:
    def __call__(self, filename, from_page=0, to_page=MAXIMUM_PAGE_NUMBER, **kwargs):
        lines = []
        try:
            self.pdf = pdf2_read(filename if isinstance(filename, str) else BytesIO(filename))
            for page in self.pdf.pages[from_page:to_page]:
                lines.extend([t for t in page.extract_text().split("\n")])
        except Exception:
            logging.exception("Outlines exception")
        self.outlines = extract_pdf_outlines(filename)

        return [(line, "") for line in lines], []

    def crop(self, ck, need_position):
        raise NotImplementedError

    @staticmethod
    def remove_tag(txt):
        raise NotImplementedError
