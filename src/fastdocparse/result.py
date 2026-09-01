"""Typed view of DocumentParser.extract()'s output.

extract() itself keeps returning a plain dict — that's what the CLI needs for
zero-friction json.dumps(), and changing it would break every existing caller and
test. This model is an opt-in convenience for callers who want attribute access
and validation instead of raw dict indexing:

    result = parser.extract(document_bytes, schema)
    typed = ExtractionResult.from_raw(result)
    typed.fields["invoice_number"].value
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class FieldResult(BaseModel):
    """Typed result for a single extracted field.

    Attributes:
        value: The extracted value, or None if not found.
        confidence: Extraction confidence level — 'high' if grounded in
            source text, 'low' otherwise.
        flags: List of status flags, e.g. 'grounded', 'ungrounded',
            'missing_required', 'invalid_format', 'failed_check'.
    """
    value: Any
    confidence: str
    flags: list[str]


class ExtractionMeta(BaseModel):
    """Metadata about the extraction run.

    Attributes:
        truncated: True if the document exceeded max_pages and was truncated.
        truncation_reason: Human-readable explanation of truncation, or None
            if the document was processed in full.
    """
    truncated: bool
    truncation_reason: str | None = None


class ExtractionResult(BaseModel):
    """Typed view of the dict returned by DocumentParser.extract().

    Prefer this over raw dict indexing when you want attribute access,
    IDE autocompletion, and Pydantic validation on the extraction output.

    Example::

        result = parser.extract(document_bytes, schema)
        typed = ExtractionResult.from_raw(result)
        print(typed.fields["invoice_number"].value)
        print(typed.meta.truncated)

    Attributes:
        meta: Extraction metadata (truncation info).
        fields: Per-field results keyed by field name.
    """
    meta: ExtractionMeta
    fields: dict[str, FieldResult]

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> ExtractionResult:
        """Build a typed result from the dict returned by DocumentParser.extract()."""
        raw = dict(raw)
        meta = raw.pop("_meta", None)
        if meta is None:
            raise ValueError(
                "Missing '_meta' key — expected the dict returned by DocumentParser.extract()."
            )
        return cls(meta=ExtractionMeta(**meta), fields={k: FieldResult(**v) for k, v in raw.items()})
