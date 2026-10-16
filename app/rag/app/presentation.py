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

import copy
import logging
import re
from io import BytesIO

from pypdf import PdfReader as pdf2_read

from deepdoc.parser import PlainParser
from deepdoc.parser.ppt_parser import RAGFlowPptParser
from rag.app.naive import PARSERS
from api.db.joint_services.tenant_model_service import get_composite_model_name_by_id
from common.constants import MAXIMUM_PAGE_NUMBER
from common.parser_config_utils import normalize_layout_recognizer
from rag.nlp import rag_tokenizer
from rag.nlp import tokenize
from rag.utils.lazy_image import ensure_pil_image, is_image_like


class PlainPdf(PlainParser):
    def __call__(self, filename, binary=None, from_page=0, to_page=MAXIMUM_PAGE_NUMBER, callback=None, **kwargs):
        self.pdf = pdf2_read(filename if binary is None else BytesIO(binary))
        page_txt = []
        for page in self.pdf.pages[from_page:to_page]:
            page_txt.append(page.extract_text())
        callback(0.9, "Parsing finished")
        return [(txt, None) for txt in page_txt], []


def chunk(filename, binary=None, from_page=0, to_page=MAXIMUM_PAGE_NUMBER, lang="Chinese", callback=None, parser_config=None, **kwargs):
    """
    The supported file formats are pdf, ppt, pptx.
    Every page will be treated as a chunk. And the thumbnail of every page will be stored.
    PPT file will be parsed by using this method automatically, setting-up for every PPT file is not necessary.
    """
    if parser_config is None:
        parser_config = {}
    eng = lang.lower() == "english"
    doc = {"docnm_kwd": filename, "title_tks": rag_tokenizer.tokenize(re.sub(r"\.[a-zA-Z]+$", "", filename))}
    doc["title_sm_tks"] = rag_tokenizer.fine_grained_tokenize(doc["title_tks"])
    res = []
    if re.search(r"\.pptx?$", filename, re.IGNORECASE):
        try:
            ppt_parser = RAGFlowPptParser()
            for pn, txt in enumerate(ppt_parser(filename if binary is None else binary, from_page, MAXIMUM_PAGE_NUMBER, callback)):
                d = copy.deepcopy(doc)
                pn += from_page
                d["doc_type_kwd"] = "image"
                d["page_num_int"] = [pn + 1]
                d["top_int"] = [0]
                d["position_int"] = [(pn + 1, 0, 0, 0, 0)]
                tokenize(d, txt, eng, language=lang)
                res.append(d)
            return res
        except Exception as e:
            logging.warning(f"python-pptx parsing failed for {filename}: {e}, trying tika as fallback")
            if callback:
                callback(0.1, "python-pptx failed, trying tika as fallback")

            try:
                from tika import parser as tika_parser
            except Exception as tika_error:
                error_msg = f"tika not available: {tika_error}. Unsupported .ppt/.pptx parsing."
                if callback:
                    callback(0.8, error_msg)
                logging.warning(f"{error_msg} for {filename}.")
                raise NotImplementedError(error_msg)

            if binary is not None:
                binary_data = binary
            else:
                with open(filename, "rb") as f:
                    binary_data = f.read()
            doc_parsed = tika_parser.from_buffer(BytesIO(binary_data))

            if doc_parsed.get("content", None) is not None:
                sections = doc_parsed["content"].split("\n")
                sections = [s for s in sections if s.strip()]

                for pn, txt in enumerate(sections):
                    d = copy.deepcopy(doc)
                    pn += from_page
                    d["doc_type_kwd"] = "text"
                    d["page_num_int"] = [pn + 1]
                    d["top_int"] = [0]
                    d["position_int"] = [(pn + 1, 0, 0, 0, 0)]
                    tokenize(d, txt, eng, language=lang)
                    res.append(d)

                if callback:
                    callback(0.8, "Finish parsing with tika.")
                return res
            else:
                error_msg = f"tika.parser got empty content from {filename}."
                if callback:
                    callback(0.8, error_msg)
                logging.warning(error_msg)
                raise NotImplementedError(error_msg)
    elif re.search(r"\.pdf$", filename, re.IGNORECASE):
        layout_recognize_raw = parser_config.get("layout_recognize", "DeepDOC")
        tenant_id = kwargs.get("tenant_id")
        if tenant_id and isinstance(layout_recognize_raw, str):
            try:
                layout_recognize_raw = get_composite_model_name_by_id(layout_recognize_raw)
            except LookupError:
                pass
        layout_recognizer, parser_model_name = normalize_layout_recognizer(layout_recognize_raw)

        if isinstance(layout_recognizer, bool):
            layout_recognizer = "DeepDOC" if layout_recognizer else "Plain Text"

        name = layout_recognizer.strip().lower()
        parser = PARSERS[name]
        callback(0.1, "Start to parse.")

        sections, _, _ = parser(
            filename=filename,
            binary=binary,
            from_page=from_page,
            to_page=to_page,
            lang=lang,
            callback=callback,

            layout_recognizer=layout_recognizer,
            mineru_llm_name=parser_model_name,
            mistral_ocr_llm_name=parser_model_name,
            paddleocr_llm_name=parser_model_name,
            **kwargs,
        )

        if not sections:
            return []

        if name in ["tcadp", "docling", "mineru", "paddleocr"]:
            parser_config["chunk_token_num"] = 0

        callback(0.8, "Finish parsing.")

        for pn, (txt, img) in enumerate(sections):
            d = copy.deepcopy(doc)
            pn += from_page
            if not is_image_like(img):
                img = None
            else:
                img = ensure_pil_image(img)
            d["image"] = img
            d["page_num_int"] = [pn + 1]
            d["top_int"] = [0]
            d["position_int"] = [(pn + 1, 0, img.size[0] if img else 0, 0, img.size[1] if img else 0)]
            tokenize(d, txt, eng, language=lang)
            res.append(d)
        return res

    raise NotImplementedError("file type not supported yet(ppt, pptx, pdf supported)")


if __name__ == "__main__":
    import sys

    def dummy(a, b):
        pass

    chunk(sys.argv[1], callback=dummy)
