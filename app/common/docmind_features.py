"""Deployment feature switches shared by DocMind API and ingestion."""

import os


def ocr_enabled() -> bool:
    return os.environ.get("DOCMIND_OCR_ENABLED", "true").strip().lower() in {
        "1", "true", "yes", "on",
    }
