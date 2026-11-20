"""OCR module facade.

Additive architecture split: this package provides a stable place for OCR services
while keeping existing OCR behavior unchanged.
"""

from .service import extract_text_from_image_ocr

__all__ = ["extract_text_from_image_ocr"]
