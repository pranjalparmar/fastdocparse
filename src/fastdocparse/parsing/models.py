"""Canonical, geometry-preserving document representation.

Coordinates are normalized to the page: ``(0, 0)`` is the top-left and
``(1, 1)`` is the bottom-right. Page width and height retain source units
(PDF points for PDFs, pixels for raster images), allowing callers to convert
normalized boxes back into source coordinates without backend-specific rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any


@dataclass(frozen=True)
class BBox:
    """A normalized rectangle with top-left origin and exclusive bottom/right edges."""

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        coords = (self.x0, self.y0, self.x1, self.y1)
        if not all(isfinite(value) for value in coords):
            raise ValueError("Bounding-box coordinates must be finite numbers")
        if not (0 <= self.x0 <= self.x1 <= 1 and 0 <= self.y0 <= self.y1 <= 1):
            raise ValueError("Bounding box must satisfy 0 <= x0 <= x1 <= 1 and 0 <= y0 <= y1 <= 1")

    def to_source(self, width: float, height: float) -> tuple[float, float, float, float]:
        """Scale this normalized box to source page coordinates."""
        if not isfinite(width) or not isfinite(height) or width <= 0 or height <= 0:
            raise ValueError("Source page width and height must be positive finite numbers")
        return self.x0 * width, self.y0 * height, self.x1 * width, self.y1 * height

    def to_dict(self) -> dict[str, float]:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}


@dataclass
class ParsedElement:
    """A positioned piece of document content, optionally containing child elements."""

    type: str
    text: str = ""
    bbox: BBox | None = None
    source: str = "unknown"
    confidence: float | None = None
    children: list[ParsedElement] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.type.strip():
            raise ValueError("Element type must not be empty")
        if not self.source.strip():
            raise ValueError("Element source must not be empty")
        if self.confidence is not None and (
            not isfinite(self.confidence) or not 0 <= self.confidence <= 1
        ):
            raise ValueError("Element confidence must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": self.type,
            "text": self.text,
            "source": self.source,
        }
        if self.bbox is not None:
            result["bbox"] = self.bbox.to_dict()
        if self.confidence is not None:
            result["confidence"] = self.confidence
        if self.children:
            result["children"] = [child.to_dict() for child in self.children]
        if self.attributes:
            result["attributes"] = dict(self.attributes)
        return result


@dataclass
class ParsedPage:
    """One page of a parsed document."""

    number: int
    width: float
    height: float
    units: str
    elements: list[ParsedElement] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValueError("Page numbers are one-based and must be positive")
        if not isfinite(self.width) or not isfinite(self.height) or self.width <= 0 or self.height <= 0:
            raise ValueError("Page width and height must be positive finite numbers")
        if not self.units.strip():
            raise ValueError("Page units must not be empty (for example, 'pt' or 'px')")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "number": self.number,
            "width": self.width,
            "height": self.height,
            "units": self.units,
            "elements": [element.to_dict() for element in self.elements],
        }
        if self.relations:
            result["relations"] = [dict(relation) for relation in self.relations]
        return result


@dataclass
class ParsedDocument:
    """A document represented as pages containing positioned semantic elements."""

    pages: list[ParsedPage] = field(default_factory=list)
    source_format: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_format": self.source_format,
            "pages": [page.to_dict() for page in self.pages],
            "metadata": dict(self.metadata),
        }
