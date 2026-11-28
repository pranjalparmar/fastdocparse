"""PDF ingestion route."""

from __future__ import annotations

import logging

from ..config import ExtractionConfig
from ..ocr import extract_text_from_image_ocr
from ..pdf_utils import extract_text_from_pdf, pdf_to_page_images

logger = logging.getLogger(__name__)


def ingest_pdf(document_bytes: bytes, structured_mode: bool, config: ExtractionConfig) -> str:
    """Extract text from PDF via digital text first, OCR fallback for scans."""
    doc_text = extract_text_from_pdf(document_bytes, max_pages=config.max_pages, structured_mode=structured_mode)
    if len(doc_text.strip()) < 30:
        logger.info(
            "Digital text layer too short (%d chars); falling back to OCR on up to %d pages.",
            len(doc_text.strip()),
            config.max_pages,
        )
        pages = pdf_to_page_images(
            document_bytes,
            max_pages=config.max_pages,
            dpi=config.pdf_render_dpi,
            max_dim=config.max_image_dim,
        )
        if pages:
            doc_text = "\n\n".join(
                extract_text_from_image_ocr(
                    page.png_bytes,
                    structured_mode=structured_mode,
                    min_confidence=config.ocr_min_confidence,
                )
                for page in pages
            )
    return doc_text
