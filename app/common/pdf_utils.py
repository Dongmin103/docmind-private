"""PDF position tags shared by retained chunk normalization."""
import logging
import re
from io import BytesIO

import pdfplumber


def remove_tag(txt):
    return re.sub(r"@@[\t0-9.-]+?##", "", txt)


def extract_positions(txt):
    poss = []
    for tag in re.findall(r"@@[0-9-]+\t[0-9.\t]+##", txt):
        pn, left, right, top, bottom = tag.strip("#").strip("@").split("\t")
        left, right, top, bottom = float(left), float(right), float(top), float(bottom)
        poss.append(([int(p) - 1 for p in pn.split("-")], left, right, top, bottom))
    return poss


def total_page_number(filename, binary=None):
    try:
        with pdfplumber.open(filename if not binary else BytesIO(binary)) as pdf:
            return len(pdf.pages)
    except Exception:
        logging.exception("total_page_number")
