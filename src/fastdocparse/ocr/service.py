"""OCR service compatibility layer.

This wraps the existing OCR implementation so new architecture modules can import
from fastdocparse.ocr without changing runtime behavior.
"""

from __future__ import annotations

from ..ocr_engine import extract_text_from_image_ocr

__all__ = ["extract_text_from_image_ocr"]
