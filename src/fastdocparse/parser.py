"""Parser module orchestrating document extraction."""
from __future__ import annotations

import asyncio
import functools
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import pymupdf

from .cache import Cache, make_cache_key
from .config import ExtractionConfig
from .grounding import (
    CrossCheckRule,
    Issue,
    _is_present,
    check_substring,
    cross_check,
    validate_field_constraints,
)
from .json_repair import parse_json_from_llm as _parse_json_from_llm
from .llm_client import LLMClient
from .ocr_engine import extract_text_from_image_ocr
from .pdf_utils import chunk_document_text, extract_text_from_pdf, pdf_to_page_images
from .prompt_compiler import compile_prompt
from .schema import Schema

logger = logging.getLogger(__name__)


class EmptyDocumentError(ValueError):
    """Raised when no text could be extracted from a document by any route."""


_LAYOUT_TAG_RE = re.compile(r"\[X:\d+\]\s*")


def _strip_layout_tags(text: str) -> str:
    """Remove the "[X:nnn]" column-position markers structured_mode adds for the LLM's
    benefit before running any grounding check against the text. Those digits are pixel
    coordinates, not document content — left in, they can splice into the middle of a
    real value's character stream (e.g. an address split across OCR lines) and break a
    fuzzy match that would otherwise succeed, flagging a correct value as ungrounded.
    """
    return _LAYOUT_TAG_RE.sub("", text)


class UnknownIngestionKindError(ValueError):
    """Raised when extract()/aextract() is given a kind with no registered handler.

    Kept distinct from a bare ValueError so callers (the CLI included) can tell "you
    passed an unregistered --kind" apart from any other ValueError that might surface
    from deeper in the pipeline (e.g. from inside a custom ingestion handler).
    """


def _merge_extracted_data(results: list[dict[str, Any]], chunks: list[str], schema: Schema) -> dict[str, Any]:
    """Merge per-chunk extractions into one result.

    List fields concatenate across chunks (unchanged). For scalar fields, when more than
    one chunk produced a non-null value, prefer whichever one is grounded in the chunk
    it actually came from — we have that chunk's source text right here, so use it
    instead of blindly taking the first chunk's answer regardless of whether it's real.
    Falls back to first-non-null when nothing grounds (matches the old behavior exactly,
    so a single-chunk document — the common case — is unaffected).
    """
    if not results:
        return {}

    merged: dict[str, Any] = {}
    for f in schema.fields:
        merged[f.name] = [] if f.type == "list" else None

    for f in schema.fields:
        if f.type == "list":
            for res in results:
                val = res.get(f.name)
                if val and isinstance(val, list):
                    merged[f.name].extend(val)
            continue

        first_non_null = None
        grounded_value = None
        for res, chunk_text in zip(results, chunks):
            val = res.get(f.name)
            if not _is_present(val):
                continue
            if first_non_null is None:
                first_non_null = val
            if grounded_value is None and check_substring(
                val, _strip_layout_tags(chunk_text), numeric=f.is_numeric, date=f.is_date
            ):
                grounded_value = val
                break
        merged[f.name] = grounded_value if grounded_value is not None else first_non_null

    return merged


# Ingestion handlers keyed by document kind. To support a new input format
# (e.g. DOCX), add a handler here and register it — no changes needed to
# DocumentParser itself.
def _ingest_pdf(document_bytes: bytes, structured_mode: bool, config: ExtractionConfig) -> str:
    doc_text = extract_text_from_pdf(document_bytes, max_pages=config.max_pages, structured_mode=structured_mode)
    if len(doc_text.strip()) < 30:
        # Scanned PDF: run local OCR on first page image
        logger.info("Digital text layer too short (%d chars); falling back to OCR on page 1.", len(doc_text.strip()))
        pages = pdf_to_page_images(
            document_bytes, max_pages=1, dpi=config.pdf_render_dpi, max_dim=config.max_image_dim
        )
        if pages:
            doc_text = extract_text_from_image_ocr(
                pages[0].png_bytes, structured_mode=structured_mode, min_confidence=config.ocr_min_confidence
            )
    return doc_text


def _ingest_image(document_bytes: bytes, structured_mode: bool, config: ExtractionConfig) -> str:
    return extract_text_from_image_ocr(
        document_bytes, structured_mode=structured_mode, min_confidence=config.ocr_min_confidence
    )


INGESTION_HANDLERS: dict[str, Callable[[bytes, bool, ExtractionConfig], str]] = {
    "pdf": _ingest_pdf,
    "image": _ingest_image,
}


def register_default_ingestion_handler(kind: str, handler: Callable[[bytes, bool, ExtractionConfig], str]) -> None:
    """Register a new default ingestion route, e.g. for DOCX/XLSX support, process-wide.

    Named distinctly from DocumentParser.register_ingestion_handler() on purpose — that
    one scopes a handler to a single instance; this one changes what *new* instances get
    by default. They used to share a name, which made it easy to call the process-wide
    one by habit when a scoped registration was actually intended.

    handler receives (document_bytes, structured_mode, config) and returns the extracted
    text. This affects DocumentParser instances created *after* this call — each instance
    copies the default registry at construction time, so it can't be silently clobbered by
    another part of the program (or another test) registering a different handler under
    the same kind later.
    """
    INGESTION_HANDLERS[kind] = handler


class DocumentParser:
    """Orchestrates document extraction."""

    def __init__(
        self,
        client: LLMClient,
        config: ExtractionConfig | None = None,
        cache: Cache | None = None,
        ingestion_handlers: dict[str, Callable[[bytes, bool, ExtractionConfig], str]] | None = None,
    ):
        self.client = client
        self.config = config or ExtractionConfig()
        self.cache = cache
        # Copied, not referenced, so registering a handler on one instance (or globally,
        # after this instance already exists) never affects an instance already in use.
        self._ingestion_handlers = dict(ingestion_handlers) if ingestion_handlers is not None else dict(INGESTION_HANDLERS)

    def register_ingestion_handler(self, kind: str, handler: Callable[[bytes, bool, ExtractionConfig], str]) -> None:
        """Register an ingestion route scoped to this DocumentParser instance only."""
        self._ingestion_handlers[kind] = handler

    def extract(
        self,
        document_bytes: bytes,
        schema: Schema,
        is_image: bool = False,
        rules: list[CrossCheckRule] | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        """Extract information from a document matching the schema.

        kind overrides routing when set (must be registered via register_ingestion_handler
        first, e.g. kind="docx"). Otherwise routing falls back to is_image, as before.
        """
        structured_mode = any(f.type == "list" for f in schema.fields)
        resolved_kind = kind or ("image" if is_image else "pdf")
        handler = self._resolve_handler(resolved_kind)
        logger.info("Extracting schema=%r via kind=%r (structured_mode=%s)", schema.name, resolved_kind, structured_mode)

        # Captured once into a local so type checkers can narrow it past None below —
        # self.cache itself can't be narrowed across statements (it's an attribute,
        # not a local), even though it never changes during a single extract() call.
        cache = self.cache
        cache_key = None
        if cache is not None and not rules:
            # Keyed on the handler itself, not just the kind name — two DocumentParser
            # instances can register different handlers under the same kind, and a
            # shared cache must not conflate them.
            cache_key = make_cache_key(document_bytes, schema, resolved_kind, self.config, handler)
            cached = cache.get(cache_key)
            if cached is not None:
                logger.info("Cache hit for schema=%r.", schema.name)
                return cached

        doc_text = handler(document_bytes, structured_mode, self.config)
        is_truncated, truncation_reason = self._check_truncation(document_bytes, resolved_kind)
        if is_truncated:
            logger.warning(truncation_reason)

        if not doc_text.strip():
            raise EmptyDocumentError("Could not extract any text from the document.")

        chunks = chunk_document_text(doc_text, max_tokens=self.config.chunk_max_tokens)
        logger.info("Document split into %d chunk(s) for extraction.", len(chunks))
        merged_data = self._run_extraction(chunks, schema)

        result = self._build_result(schema, merged_data, doc_text, rules, is_truncated, truncation_reason)

        if cache_key is not None and cache is not None:
            cache.set(cache_key, result)

        return result

    async def aextract(
        self,
        document_bytes: bytes,
        schema: Schema,
        is_image: bool = False,
        rules: list[CrossCheckRule] | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        """Async wrapper around extract(). The work itself is still synchronous (OCR,
        PDF parsing, and the LLM SDK calls are all blocking) — this runs it in a
        background thread so an asyncio event loop (e.g. inside a FastAPI route) isn't
        blocked while it happens, not because the underlying pipeline became non-blocking.
        """
        loop = asyncio.get_running_loop()
        call = functools.partial(self.extract, document_bytes, schema, is_image=is_image, rules=rules, kind=kind)
        return await loop.run_in_executor(None, call)

    def _resolve_handler(self, kind: str) -> Callable[[bytes, bool, ExtractionConfig], str]:
        handler = self._ingestion_handlers.get(kind)
        if handler is None:
            raise UnknownIngestionKindError(f"No ingestion handler registered for document kind {kind!r}")
        return handler

    def _check_truncation(self, document_bytes: bytes, kind: str) -> tuple[bool, str | None]:
        if kind != "pdf":
            return False, None
        try:
            doc = pymupdf.open(stream=document_bytes, filetype="pdf")
            try:
                if len(doc) > self.config.max_pages:
                    return True, f"Document is {len(doc)} pages long, truncated to {self.config.max_pages} pages."
                return False, None
            finally:
                doc.close()
        except (pymupdf.EmptyFileError, pymupdf.FileDataError, RuntimeError):
            return False, None

    def _run_extraction(self, chunks: list[str], schema: Schema) -> dict[str, Any]:
        prompt_template = compile_prompt(schema)
        max_workers = self.config.max_concurrent_chunks

        if max_workers <= 1 or len(chunks) <= 1:
            # Default path: identical to the original sequential loop, no thread pool
            # involved — keeps behavior (and mock call ordering in tests) unchanged.
            parsed_chunks = [_parse_json_from_llm(self.client.extract(prompt_template, chunk)) for chunk in chunks]
        else:
            def process(chunk: str) -> dict[str, Any]:
                return _parse_json_from_llm(self.client.extract(prompt_template, chunk))

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                parsed_chunks = list(executor.map(process, chunks))

        return _merge_extracted_data(parsed_chunks, chunks, schema)

    def _build_result(
        self,
        schema: Schema,
        merged_data: dict[str, Any],
        doc_text: str,
        rules: list | None,
        is_truncated: bool,
        truncation_reason: str | None,
    ) -> dict[str, Any]:
        issues = cross_check(schema, merged_data, rules) + validate_field_constraints(schema, merged_data)
        issues_by_field: dict[str, list[Issue]] = {}
        for issue in issues:
            issues_by_field.setdefault(issue.field, []).append(issue)
        issue_kind_to_flag = {
            "cross_check": "failed_check",
            "missing_required": "missing_required",
            "invalid_format": "invalid_format",
        }

        final_result: dict[str, Any] = {
            "_meta": {
                "truncated": is_truncated,
                "truncation_reason": truncation_reason,
            }
        }
        grounding_text = _strip_layout_tags(doc_text)
        for f in schema.fields:
            value = merged_data.get(f.name, None)
            flags = []
            confidence = "low"

            if _is_present(value):
                if check_substring(value, grounding_text, numeric=f.is_numeric, date=f.is_date):
                    flags.append("grounded")
                    confidence = "high"
                else:
                    flags.append("ungrounded")

            for issue in issues_by_field.get(f.name, []):
                flag = issue_kind_to_flag.get(issue.kind, "failed_check")
                if flag not in flags:
                    flags.append(flag)

            final_result[f.name] = {
                "value": value,
                "confidence": confidence,
                "flags": flags,
            }

        return final_result
