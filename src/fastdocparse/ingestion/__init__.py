"""Document ingestion routes.

This package hosts ingestion handlers by document kind (pdf/image/...).
"""

from .image_ingest import ingest_image
from .pdf_ingest import ingest_pdf

__all__ = ["ingest_image", "ingest_pdf"]
