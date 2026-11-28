"""Image ingestion route."""

from __future__ import annotations

from ..config import ExtractionConfig
from ..ocr import extract_text_from_image_ocr


def ingest_image(document_bytes: bytes, structured_mode: bool, config: ExtractionConfig) -> str:
    """Extract text from image bytes using OCR."""
    return extract_text_from_image_ocr(
        document_bytes,
        structured_mode=structured_mode,
        min_confidence=config.ocr_min_confidence,
    )
