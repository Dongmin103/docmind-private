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

import logging
import re
from functools import reduce
from io import BytesIO
from timeit import default_timer as timer
from docx import Document
from docx.opc.pkgreader import _SerializedRelationships, _SerializedRelationship
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph
from docx.opc.oxml import parse_xml
from markdown import markdown
from PIL import Image
from common.token_utils import num_tokens_from_string

from common.constants import MAXIMUM_PAGE_NUMBER
from rag.utils.file_utils import extract_embed_file, extract_links_from_pdf, extract_links_from_docx, extract_html
from deepdoc.parser import DocxParser, EpubParser, ExcelParser, HtmlParser, JsonParser, MarkdownElementExtractor, MarkdownParser, TxtParser
from deepdoc.parser.plain_pdf_parser import PlainParser
from common.float_utils import normalize_overlapped_percent
from common.parser_config_utils import normalize_layout_recognizer
from common.text_utils import normalize_arabic_presentation_forms
from rag.nlp import (
    concat_img,
    find_codec,
    naive_merge,
    naive_merge_with_images,
    naive_merge_docx,
    rag_tokenizer,
    tokenize_chunks,
    doc_tokenize_chunks_with_images,
    tokenize_table,
    append_context2table_image4pdf,
    tokenize_chunks_with_images,
)  # noqa: F401


def _is_short_header(text, max_tokens=50):
    """
    Check if text is a short markdown header.

    Args:
        text: The text to check
        max_tokens: Maximum tokens for a header to be considered "short"

    Returns:
        bool: True if text is a short markdown header, False otherwise
    """
    if not text or not text.strip():
        return False

    # Check if it matches markdown header pattern: 1-6 # followed by space
    if not re.match(r"^#{1,6}\s+", text.strip()):
        return False

    # Check if token count is below threshold
    return num_tokens_from_string(text) < max_tokens


def _normalize_section_text_for_rtl_presentation_forms(sections):
    if not sections:
        return sections

    normalized_sections = []
    for section in sections:
        if isinstance(section, tuple):
            if not section:
                normalized_sections.append(section)
                continue
            text = section[0]
            normalized_text = normalize_arabic_presentation_forms(text)
            normalized_sections.append((normalized_text, *section[1:]))
            continue
        if isinstance(section, list):
            if not section:
                normalized_sections.append(section)
                continue
            text = section[0]
            normalized_text = normalize_arabic_presentation_forms(text)
            normalized_sections.append([normalized_text, *section[1:]])
            continue
        normalized_sections.append(normalize_arabic_presentation_forms(section))

    return normalized_sections


def by_plaintext(filename, binary=None, from_page=0, to_page=MAXIMUM_PAGE_NUMBER, callback=None, **kwargs):
    selection = kwargs.get("layout_recognizer", "Plain Text")
    normalize_layout_recognizer(selection)
    if selection not in (None, "", False) and str(selection).replace(" ", "").lower() != "plaintext":
        raise ValueError(f"Unsupported PDF attachment parser: {selection}")
    pdf_parser = PlainParser()
    sections, tables = pdf_parser(filename if binary is None else binary, from_page=from_page, to_page=to_page, callback=callback)
    return sections, tables, pdf_parser


PARSERS = {"plaintext": by_plaintext, "plain text": by_plaintext}


def _dispatch_pdf_parser(parser_config, layout_recognize_override=None):
    selection = parser_config.get("layout_recognize", "Plain Text") if layout_recognize_override is None else layout_recognize_override
    normalize_layout_recognizer(selection)
    name = "plaintext" if selection in (None, "", False) else str(selection).replace(" ", "").lower()
    if name != "plaintext":
        raise ValueError(f"Unsupported PDF attachment parser: {selection}")
    return by_plaintext, name, "Plain Text"


class Docx(DocxParser):
    def __init__(self):
        pass

    def __clean(self, line):
        line = re.sub(r"\u3000", " ", line).strip()
        return line

    def __get_nearest_title(self, table_index, filename):
        """Get the hierarchical title structure before the table"""
        import re
        from docx.text.paragraph import Paragraph

        titles = []
        blocks = []

        # Get document name from filename parameter
        doc_name = re.sub(r"\.[a-zA-Z]+$", "", filename)
        if not doc_name:
            doc_name = "Untitled Document"

        # Collect all document blocks while maintaining document order
        try:
            # Iterate through all paragraphs and tables in document order
            for i, block in enumerate(self.doc._element.body):
                if block.tag.endswith("p"):  # Paragraph
                    p = Paragraph(block, self.doc)
                    blocks.append(("p", i, p))
                elif block.tag.endswith("tbl"):  # Table
                    blocks.append(("t", i, None))  # Table object will be retrieved later
        except Exception as e:
            logging.error(f"Error collecting blocks: {e}")
            return ""

        # Find the target table position
        target_table_pos = -1
        table_count = 0
        for i, (block_type, pos, _) in enumerate(blocks):
            if block_type == "t":
                if table_count == table_index:
                    target_table_pos = pos
                    break
                table_count += 1

        if target_table_pos == -1:
            return ""  # Target table not found

        # Find the nearest heading paragraph in reverse order
        nearest_title = None
        for i in range(len(blocks) - 1, -1, -1):
            block_type, pos, block = blocks[i]
            if pos >= target_table_pos:  # Skip blocks after the table
                continue

            if block_type != "p":
                continue

            if block.style and block.style.name and re.search(r"Heading\s*(\d+)", block.style.name, re.I):
                try:
                    level_match = re.search(r"(\d+)", block.style.name)
                    if level_match:
                        level = int(level_match.group(1))
                        if level <= 7:  # Support up to 7 heading levels
                            title_text = block.text.strip()
                            if title_text:  # Avoid empty titles
                                nearest_title = (level, title_text)
                                break
                except Exception as e:
                    logging.error(f"Error parsing heading level: {e}")

        if nearest_title:
            # Add current title
            titles.append(nearest_title)
            current_level = nearest_title[0]

            # Find all parent headings, allowing cross-level search
            while current_level > 1:
                found = False
                for i in range(len(blocks) - 1, -1, -1):
                    block_type, pos, block = blocks[i]
                    if pos >= target_table_pos:  # Skip blocks after the table
                        continue

                    if block_type != "p":
                        continue

                    if block.style and re.search(r"Heading\s*(\d+)", block.style.name, re.I):
                        try:
                            level_match = re.search(r"(\d+)", block.style.name)
                            if level_match:
                                level = int(level_match.group(1))
                                # Find any heading with a higher level
                                if level < current_level:
                                    title_text = block.text.strip()
                                    if title_text:  # Avoid empty titles
                                        titles.append((level, title_text))
                                        current_level = level
                                        found = True
                                        break
                        except Exception as e:
                            logging.error(f"Error parsing parent heading: {e}")

                if not found:  # Break if no parent heading is found
                    break

            # Sort by level (ascending, from highest to lowest)
            titles.sort(key=lambda x: x[0])
            # Organize titles (from highest to lowest)
            hierarchy = [doc_name] + [t[1] for t in titles]
            return " > ".join(hierarchy)

        return ""

    def __call__(self, filename, binary=None, from_page=0, to_page=MAXIMUM_PAGE_NUMBER):
        self.doc = Document(filename) if binary is None else Document(BytesIO(binary))
        pn = 0
        lines = []
        last_image = None
        table_idx = 0

        def flush_last_image():
            nonlocal last_image, lines
            if last_image is not None:
                lines.append({"text": "", "image": last_image, "table": None, "style": "Image"})
                last_image = None

        for block in self.doc._element.body:
            if pn > to_page:
                break

            if block.tag.endswith("p"):
                p = Paragraph(block, self.doc)

                if from_page <= pn < to_page:
                    text = p.text.strip()
                    style_name = p.style.name if p.style else ""

                    if text:
                        if style_name == "Caption":
                            former_image = None

                            if lines and lines[-1].get("image") and lines[-1].get("style") != "Caption":
                                former_image = lines[-1].get("image")
                                lines.pop()

                            elif last_image is not None:
                                former_image = last_image
                                last_image = None

                            lines.append(
                                {
                                    "text": self.__clean(text),
                                    "image": former_image if former_image else None,
                                    "table": None,
                                }
                            )

                        else:
                            flush_last_image()
                            lines.append(
                                {
                                    "text": self.__clean(text),
                                    "image": None,
                                    "table": None,
                                }
                            )

                            current_image = self.get_picture(self.doc, p)
                            if current_image is not None:
                                lines.append(
                                    {
                                        "text": "",
                                        "image": current_image,
                                        "table": None,
                                    }
                                )

                    else:
                        current_image = self.get_picture(self.doc, p)
                        if current_image is not None:
                            last_image = current_image

                for run in p.runs:
                    xml = run._element.xml
                    if "lastRenderedPageBreak" in xml:
                        pn += 1
                        continue
                    if "w:br" in xml and 'type="page"' in xml:
                        pn += 1

            elif block.tag.endswith("tbl"):
                if pn < from_page or pn > to_page:
                    table_idx += 1
                    continue

                flush_last_image()
                tb = DocxTable(block, self.doc)
                title = self.__get_nearest_title(table_idx, filename)
                html = "<table>"
                if title:
                    html += f"<caption>Table Location: {title}</caption>"
                for r in tb.rows:
                    html += "<tr>"
                    col_idx = 0
                    try:
                        while col_idx < len(r.cells):
                            span = 1
                            c = r.cells[col_idx]
                            for j in range(col_idx + 1, len(r.cells)):
                                if c.text == r.cells[j].text:
                                    span += 1
                                    col_idx = j
                                else:
                                    break
                            col_idx += 1
                            html += f"<td>{c.text}</td>" if span == 1 else f"<td colspan='{span}'>{c.text}</td>"
                    except Exception as e:
                        logging.warning(f"Error parsing table, ignore: {e}")
                    html += "</tr>"
                html += "</table>"
                lines.append({"text": "", "image": None, "table": html})
                table_idx += 1

        flush_last_image()
        new_line = [(line.get("text"), line.get("image"), line.get("table")) for line in lines]

        return new_line

    def to_markdown(self, filename=None, binary=None, inline_images: bool = True):
        """
        This function uses mammoth, licensed under the BSD 2-Clause License.
        """

        import base64
        import uuid

        import mammoth
        from markdownify import markdownify

        docx_file = BytesIO(binary) if binary is not None else open(filename, "rb")

        def _convert_image_to_base64(image):
            try:
                with image.open() as image_file:
                    image_bytes = image_file.read()
                encoded = base64.b64encode(image_bytes).decode("utf-8")
                base64_url = f"data:{image.content_type};base64,{encoded}"

                alt_name = "image"
                alt_name = f"img_{uuid.uuid4().hex[:8]}"

                return {"src": base64_url, "alt": alt_name}
            except Exception as e:
                logging.warning(f"Failed to convert image to base64: {e}")
                return {"src": "", "alt": "image"}

        try:
            if inline_images:
                result = mammoth.convert_to_html(docx_file, convert_image=mammoth.images.img_element(_convert_image_to_base64))
            else:
                result = mammoth.convert_to_html(docx_file)

            html = result.value

            markdown_text = markdownify(html)
            return markdown_text

        finally:
            if binary is None:
                docx_file.close()


# Maximum number of HTTP redirects followed when fetching a remote image
# referenced by a markdown document (each hop is SSRF-validated).
MAX_IMAGE_REDIRECTS = 5


class Markdown(MarkdownParser):
    def md_to_html(self, sections):
        if not sections:
            return []
        if isinstance(sections, type("")):
            text = sections
        elif isinstance(sections[0], type("")):
            text = sections[0]
        else:
            return []

        from bs4 import BeautifulSoup

        html_content = markdown(text)
        soup = BeautifulSoup(html_content, "html.parser")
        return soup

    def get_hyperlink_urls(self, soup):
        if soup:
            return set([a.get("href") for a in soup.find_all("a") if a.get("href")])
        return []

    def extract_image_urls_with_lines(self, text):
        md_img_re = re.compile(r"!\[[^\]]*\]\(([^)\s]+)")
        html_img_re = re.compile(r'src=["\\\']([^"\\\'>\\s]+)', re.IGNORECASE)
        urls = []
        seen = set()
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            for url in md_img_re.findall(line):
                if (url, idx) not in seen:
                    urls.append({"url": url, "line": idx})
                    seen.add((url, idx))
            for url in html_img_re.findall(line):
                if (url, idx) not in seen:
                    urls.append({"url": url, "line": idx})
                    seen.add((url, idx))

        # cross-line
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(text, "html.parser")
            newline_offsets = [m.start() for m in re.finditer(r"\n", text)] + [len(text)]
            for img_tag in soup.find_all("img"):
                src = img_tag.get("src")
                if not src:
                    continue

                tag_str = str(img_tag)
                pos = text.find(tag_str)
                if pos == -1:
                    # fallback
                    pos = max(text.find(src), 0)
                line_no = 0
                for i, off in enumerate(newline_offsets):
                    if pos <= off:
                        line_no = i
                        break
                if (src, line_no) not in seen:
                    urls.append({"url": src, "line": line_no})
                    seen.add((src, line_no))
        except Exception as e:
            logging.error("Failed to extract image urls: {}".format(e))
            pass

        return urls

    def load_images_from_urls(self, urls, cache=None):
        import requests
        from pathlib import Path
        from urllib.parse import urljoin

        from common.ssrf_guard import assert_url_is_safe, pin_dns

        cache = cache or {}
        images = []
        for url in urls:
            if url in cache:
                if cache[url]:
                    images.append(cache[url])
                continue
            img_obj = None
            try:
                if url.startswith(("http://", "https://")):
                    # SSRF guard: image references come from the (untrusted) uploaded
                    # document, so validate and DNS-pin every hop before connecting.
                    # Otherwise a markdown image like ![x](http://169.254.169.254/...)
                    # would make the server fetch internal services / cloud metadata.
                    # Redirects are followed manually so each hop is re-validated.
                    current_hostname, current_ip = assert_url_is_safe(url)
                    current_url = url
                    response = None
                    try:
                        for _ in range(MAX_IMAGE_REDIRECTS + 1):
                            # Release the previous hop before opening the next: with
                            # stream=True the connection isn't returned to the pool
                            # until the body is read or the response is closed.
                            if response is not None:
                                response.close()
                            with pin_dns(current_hostname, current_ip):
                                response = requests.get(current_url, stream=True, timeout=30, allow_redirects=False)
                            if response.status_code not in (301, 302, 303, 307, 308):
                                break
                            location = response.headers.get("Location")
                            if not location:
                                break
                            current_url = urljoin(current_url, location)
                            current_hostname, current_ip = assert_url_is_safe(current_url)
                        else:
                            raise ValueError(f"Exceeded {MAX_IMAGE_REDIRECTS} redirects fetching {url!r}")
                        if response.status_code == 200 and response.headers.get("Content-Type", "").startswith("image/"):
                            img_obj = Image.open(BytesIO(response.content)).convert("RGB")
                    finally:
                        # Always release the final/streamed response, including the
                        # non-image and redirect-cap paths where the body is unread.
                        if response is not None:
                            response.close()
                else:
                    local_path = Path(url)
                    if local_path.exists():
                        img_obj = Image.open(url).convert("RGB")
                    else:
                        logging.warning(f"Local image file not found: {url}")
            except Exception as e:
                logging.error(f"Failed to download/open image from {url}: {e}")
            cache[url] = img_obj
            if img_obj:
                images.append(img_obj)
        return images, cache

    def __call__(self, filename, binary=None, separate_tables=True, delimiter=None, return_section_images=False):
        """Parse markdown into text sections and optional standalone table chunks."""
        if binary is not None:
            encoding = find_codec(binary)
            txt = binary.decode(encoding, errors="ignore")
        else:
            with open(filename, "r") as f:
                txt = f.read()

        remainder, tables = self.extract_tables_and_remainder(f"{txt}\n", separate_tables=separate_tables)
        parsing_text = remainder
        extractor = MarkdownElementExtractor(parsing_text)
        image_refs = self.extract_image_urls_with_lines(parsing_text)
        element_sections = extractor.extract_elements(delimiter, include_meta=True)

        sections = []
        section_images = []
        image_cache = {}
        for element in element_sections:
            content = element["content"]
            start_line = element["start_line"]
            end_line = element["end_line"]
            urls_in_section = [ref["url"] for ref in image_refs if start_line <= ref["line"] <= end_line]
            imgs = []
            if urls_in_section:
                imgs, image_cache = self.load_images_from_urls(urls_in_section, image_cache)
            combined_image = None
            if imgs:
                combined_image = reduce(concat_img, imgs) if len(imgs) > 1 else imgs[0]
            sections.append((content, ""))
            section_images.append(combined_image)

        tbls = []
        if separate_tables:
            for table in tables:
                tbls.append(((None, markdown(table, extensions=["markdown.extensions.tables"])), ""))
        if return_section_images:
            return sections, tbls, section_images
        return sections, tbls


def load_from_xml_v2(baseURI, rels_item_xml):
    """
    Return |_SerializedRelationships| instance loaded with the
    relationships contained in *rels_item_xml*. Returns an empty
    collection if *rels_item_xml* is |None|.
    """
    srels = _SerializedRelationships()
    if rels_item_xml is not None:
        rels_elm = parse_xml(rels_item_xml)
        for rel_elm in rels_elm.Relationship_lst:
            if rel_elm.target_ref in ("../NULL", "NULL") or rel_elm.target_ref.startswith("#"):
                continue
            srels._srels.append(_SerializedRelationship(baseURI, rel_elm))
    return srels


def chunk(filename, binary=None, from_page=0, to_page=MAXIMUM_PAGE_NUMBER, lang="Chinese", callback=None, **kwargs):
    """
    Supported file formats are docx, pdf, excel, txt.
    This method apply the naive ways to chunk files.
    Successive text will be sliced into pieces using 'delimiter'.
    Next, these successive pieces are merge into chunks whose token number is no more than 'Max token number'.
    """
    urls = set()
    url_res = []
    excel_records = None
    excel_display_grid = None
    excel_original_format = None

    lang = lang or "Chinese"
    is_english = lang.lower() == "english"  # is_english(cks)
    parser_config = kwargs.get("parser_config", {"chunk_token_num": 512, "delimiter": "\n!?。；！？", "layout_recognize": "Plain Text", "analyze_hyperlink": True})

    child_deli = (parser_config.get("children_delimiter") or "").encode("utf-8").decode("unicode_escape").encode("latin1").decode("utf-8")
    cust_child_deli = re.findall(r"`([^`]+)`", child_deli)
    child_deli = "|".join(re.sub(r"`([^`]+)`", "", child_deli))
    if cust_child_deli:
        cust_child_deli = sorted(set(cust_child_deli), key=lambda x: -len(x))
        cust_child_deli = "|".join(re.escape(t) for t in cust_child_deli if t)
        child_deli += cust_child_deli

    is_markdown = False
    table_context_size = max(0, int(parser_config.get("table_context_size", 0) or 0))
    image_context_size = max(0, int(parser_config.get("image_context_size", 0) or 0))

    doc = {"docnm_kwd": filename, "title_tks": rag_tokenizer.tokenize(re.sub(r"\.[a-zA-Z]+$", "", filename))}
    doc["title_sm_tks"] = rag_tokenizer.fine_grained_tokenize(doc["title_tks"])
    res = []
    pdf_parser = None
    section_images = None

    is_root = kwargs.get("is_root", True)
    embed_res = []
    if is_root:
        # Only extract embedded files at the root call
        embeds = []
        if binary is not None:
            embeds = extract_embed_file(binary)
        else:
            raise Exception("Embedding extraction from file path is not supported.")

        # Recursively chunk each embedded file and collect results
        for embed_filename, embed_bytes in embeds:
            try:
                sub_res = chunk(embed_filename, binary=embed_bytes, lang=lang, callback=callback, is_root=False, **kwargs) or []
                embed_res.extend(sub_res)
            except Exception as e:
                error_msg = f"Failed to chunk embed {embed_filename}: {e}"
                logging.error(error_msg)
                if callback:
                    callback(0.05, error_msg)
                continue

    if re.search(r"\.docx$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")
        if parser_config.get("analyze_hyperlink", False) and is_root:
            urls = extract_links_from_docx(binary)
            for index, url in enumerate(urls):
                html_bytes, metadata = extract_html(url)
                if not html_bytes:
                    continue
                try:
                    sub_url_res = chunk(url, html_bytes, callback=callback, lang=lang, is_root=False, **kwargs)
                except Exception as e:
                    logging.info(f"Failed to chunk url in registered file type {url}: {e}")
                    sub_url_res = chunk(f"{index}.html", html_bytes, callback=callback, lang=lang, is_root=False, **kwargs)
                url_res.extend(sub_url_res)

        # fix "There is no item named 'word/NULL' in the archive", referring to https://github.com/python-openxml/python-docx/issues/1105#issuecomment-1298075246
        _SerializedRelationships.load_from_xml = load_from_xml_v2

        # sections = (text, image, tables)
        sections = Docx()(filename, binary)
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)

        # chunks list[dict]
        # images list - index of image chunk in chunks
        chunks, images = naive_merge_docx(sections, int(parser_config.get("chunk_token_num", 128)), parser_config.get("delimiter", "\n!?。；！？"), table_context_size, image_context_size)


        callback(0.8, "Finish parsing.")
        st = timer()

        res.extend(doc_tokenize_chunks_with_images(chunks, doc, is_english, child_delimiters_pattern=child_deli, language=lang))
        logging.info("naive_merge({}): {}".format(filename, timer() - st))
        res.extend(embed_res)
        res.extend(url_res)
        return res

    elif re.search(r"\.pdf$", filename, re.IGNORECASE):
        layout_recognize_raw = parser_config.get("layout_recognize", "DeepDOC")
        parser, name, layout_recognizer = _dispatch_pdf_parser(parser_config, layout_recognize_override=layout_recognize_raw)

        if parser_config.get("analyze_hyperlink", False) and is_root:
            urls = extract_links_from_pdf(binary)
        callback(0.1, "Start to parse.")

        sections, tables, pdf_parser = parser(
            filename=filename,
            binary=binary,
            from_page=from_page,
            to_page=to_page,
            lang=lang,
            callback=callback,
            layout_recognizer=layout_recognizer,
            **kwargs,
        )
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)

        if not sections and not tables:
            return []

        if table_context_size or image_context_size:
            tables = append_context2table_image4pdf(
                sections,
                tables,
                image_context_size,
                section_page_offset=0,
            )

        res = tokenize_table(tables, doc, is_english, language=lang)
        callback(0.8, "Finish parsing.")

    elif re.search(r"\.(csv|xlsx?)$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")

        # Default DeepDOC parser
        excel_parser = ExcelParser()
        if parser_config.get("html4excel"):
            sections = [(_, "") for _ in excel_parser.html(binary, 12) if _]
            parser_config["chunk_token_num"] = 0
        else:
            sections = [(_, "") for _ in excel_parser(binary) if _]
            if parser_config.get("excel_field_chunking", False) and re.search(r"\.xlsx?$", filename, re.IGNORECASE):
                workbook = excel_parser._load_excel_to_workbook(binary)
                excel_records = [record for sheet in workbook.sheetnames
                                 for record in excel_parser._worksheet_records(workbook[sheet])]
                if parser_config.get("excel_display_html", False):
                    from rag.app.excel_chunker import load_excel_canonical_grid

                    excel_display_grid = load_excel_canonical_grid(binary, filename)
                    excel_original_format = filename.rsplit(".", 1)[-1].lower()
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)


    elif re.search(r"\.(txt|py|js|java|c|cpp|h|php|go|ts|sh|cs|kt|sql)$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")
        sections = TxtParser()(filename, binary, parser_config.get("chunk_token_num", 128), parser_config.get("delimiter", "\n!?;。；！？"))
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)
        logging.info("TxtParser produced %d sections for %s", len(sections), filename)
        callback(0.8, "Finish parsing.")

    elif re.search(r"\.(md|markdown|mdx)$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")
        markdown_parser = Markdown(int(parser_config.get("chunk_token_num", 128)))
        sections, tables, section_images = markdown_parser(
            filename,
            binary,
            separate_tables=False,
            delimiter=parser_config.get("delimiter", "\n!?;。；！？"),
            return_section_images=True,
        )
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)

        is_markdown = True


    elif re.search(r"\.(htm|html)$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")
        chunk_token_num = int(parser_config.get("chunk_token_num", 128))
        sections = HtmlParser()(filename, binary, chunk_token_num)
        sections = [(_, "") for _ in sections if _]
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)
        callback(0.8, "Finish parsing.")

    elif re.search(r"\.epub$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")
        chunk_token_num = int(parser_config.get("chunk_token_num", 128))
        sections = EpubParser()(filename, binary, chunk_token_num)
        sections = [(_, "") for _ in sections if _]
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)
        callback(0.8, "Finish parsing.")

    elif re.search(r"\.(json|jsonl|ldjson)$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")
        chunk_token_num = int(parser_config.get("chunk_token_num", 128))
        sections = JsonParser(chunk_token_num)(binary)
        sections = [(_, "") for _ in sections if _]
        sections = _normalize_section_text_for_rtl_presentation_forms(sections)
        callback(0.8, "Finish parsing.")

    elif re.search(r"\.doc$", filename, re.IGNORECASE):
        callback(0.1, "Start to parse.")

        try:
            from tika import parser as tika_parser
        except Exception as e:
            callback(0.8, f"tika not available: {e}. Unsupported .doc parsing.")
            logging.warning(f"tika not available: {e}. Unsupported .doc parsing for {filename}.")
            return []

        binary = BytesIO(binary)
        doc_parsed = tika_parser.from_buffer(binary)
        if doc_parsed.get("content", None) is not None:
            sections = doc_parsed["content"].split("\n")
            sections = [(_, "") for _ in sections if _]
            sections = _normalize_section_text_for_rtl_presentation_forms(sections)
            callback(0.8, "Finish parsing.")
        else:
            error_msg = f"tika.parser got empty content from {filename}."
            callback(0.8, error_msg)
            logging.warning(error_msg)
            return []
    else:
        raise NotImplementedError("file type not supported yet(pdf, xlsx, doc, docx, txt supported)")

    st = timer()
    overlapped_percent = normalize_overlapped_percent(parser_config.get("overlapped_percent", 0))

    if is_markdown:
        merged_chunks = []
        merged_images = []
        chunk_limit = max(0, int(parser_config.get("chunk_token_num", 128)))

        current_text = ""
        current_tokens = 0
        current_image = None

        for idx, sec in enumerate(sections):
            text = sec[0] if isinstance(sec, tuple) else sec
            sec_tokens = num_tokens_from_string(text)
            sec_image = section_images[idx] if section_images and idx < len(section_images) else None

            # Don't finalize chunk if current_text is a short header (force merge with next section)
            if current_text and not _is_short_header(current_text) and current_tokens + sec_tokens > chunk_limit:
                merged_chunks.append(current_text)
                merged_images.append(current_image)
                overlap_part = ""
                if overlapped_percent > 0:
                    overlap_len = int(len(current_text) * overlapped_percent / 100)
                    if overlap_len > 0:
                        overlap_part = current_text[-overlap_len:]
                current_text = overlap_part
                current_tokens = num_tokens_from_string(current_text)
                current_image = current_image if overlap_part else None

            if current_text:
                current_text += "\n" + text
            else:
                current_text = text
            current_tokens += sec_tokens

            if sec_image:
                current_image = concat_img(current_image, sec_image) if current_image else sec_image

        if current_text:
            merged_chunks.append(current_text)
            merged_images.append(current_image)

        chunks = merged_chunks
        has_images = merged_images and any(img is not None for img in merged_images)

        if has_images:
            res.extend(tokenize_chunks_with_images(chunks, doc, is_english, merged_images, child_delimiters_pattern=child_deli, language=lang))
        else:
            res.extend(tokenize_chunks(chunks, doc, is_english, pdf_parser, child_delimiters_pattern=child_deli, language=lang))
    else:
        if section_images:
            if all(image is None for image in section_images):
                section_images = None

        if section_images:
            chunks, images = naive_merge_with_images(sections, section_images, int(parser_config.get("chunk_token_num", 128)), parser_config.get("delimiter", "\n!?。；！？"), overlapped_percent)
            res.extend(tokenize_chunks_with_images(chunks, doc, is_english, images, child_delimiters_pattern=child_deli, language=lang))
        else:
            if excel_records is not None:
                from rag.app.excel_chunker import chunk_excel_record_groups, excel_embedding_counter

                policy = parser_config.get("excel_table_chunking_policy")
                if policy is None:
                    excel_groups = chunk_excel_record_groups(
                        excel_records,
                        int(parser_config.get("excel_chunk_token_num", parser_config.get("chunk_token_num", 128))),
                    )
                elif policy == "whole_table_complete_rows_v1":
                    excel_budget, excel_count_tokens = excel_embedding_counter(parser_config)
                    excel_groups = chunk_excel_record_groups(
                        excel_records, excel_budget, excel_count_tokens, whole_table_rows=True
                    )
                else:
                    raise ValueError(f"Unsupported excel_table_chunking_policy: {policy!r}")
                chunks = [group["text"] for group in excel_groups]
            else:
                excel_groups = None
                chunks = naive_merge(sections, int(parser_config.get("chunk_token_num", 128)), parser_config.get("delimiter", "\n!?。；！？"), overlapped_percent)

            tokenized_chunks = tokenize_chunks(chunks, doc, is_english, pdf_parser, child_delimiters_pattern=child_deli, language=lang)
            if excel_groups is not None and excel_display_grid is not None:
                from rag.app.excel_chunker import attach_excel_display_html

                attach_excel_display_html(tokenized_chunks, excel_groups, excel_display_grid, excel_original_format)
            res.extend(tokenized_chunks)

    if urls and parser_config.get("analyze_hyperlink", False) and is_root:
        for index, url in enumerate(urls):
            html_bytes, metadata = extract_html(url)
            if not html_bytes:
                continue
            try:
                sub_url_res = chunk(url, html_bytes, callback=callback, lang=lang, is_root=False, **kwargs)
            except Exception as e:
                logging.info(f"Failed to chunk url in registered file type {url}: {e}")
                sub_url_res = chunk(f"{index}.html", html_bytes, callback=callback, lang=lang, is_root=False, **kwargs)
            url_res.extend(sub_url_res)

    logging.info("naive_merge({}): {}".format(filename, timer() - st))

    if embed_res:
        res.extend(embed_res)
    if url_res:
        res.extend(url_res)
    # if table_context_size or image_context_size:
    #    attach_media_context(res, table_context_size, image_context_size)

    # Attach PDF outline as transient metadata on the first chunk.
    # the shared task handler will extract and persist it as document metadata.
    if res and pdf_parser and getattr(pdf_parser, "outlines", None):
        res[0]["__outline__"] = [{"title": title, "depth": depth} for title, depth, *_ in pdf_parser.outlines]

    return res


if __name__ == "__main__":
    import sys

    def dummy(prog=None, msg=""):
        pass

    chunk(sys.argv[1], from_page=0, to_page=10, callback=dummy)
