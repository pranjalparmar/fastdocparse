"""Backward-compatible import for document extraction orchestration.

The canonical implementation remains in :mod:`fastdocparse.parser` until the
package split can be completed without changing established patch points.
"""

from __future__ import annotations

from ..parser import (
    DocumentParser,
    EmptyDocumentError,
    UnknownIngestionKindError,
    register_default_ingestion_handler,
)

__all__ = [
    "DocumentParser",
    "EmptyDocumentError",
    "UnknownIngestionKindError",
    "register_default_ingestion_handler",
]
