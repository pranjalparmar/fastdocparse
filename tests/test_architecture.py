"""Tests for the architecture-level additions: caching, concurrency, grounded merge,
the public ingestion-registry extension point, and the deduplicated example schema.
"""

import asyncio
import dataclasses
import json
from unittest.mock import MagicMock, patch

import pytest

from fastdocparse.cache import InMemoryCache, make_cache_key
from fastdocparse.config import ExtractionConfig
from fastdocparse.example_schemas import INVOICE_SCHEMA
from fastdocparse.parser import (
    INGESTION_HANDLERS,
    DocumentParser,
    register_default_ingestion_handler,
)
from fastdocparse.schema import Field, Schema


def test_cache_hit_avoids_second_llm_call():
    schema = Schema(name="Test", fields=[Field(name="total", description="total")])
    mock_client = MagicMock()
    mock_client.extract.return_value = '{"total": "15.00"}'
    cache = InMemoryCache()
    parser = DocumentParser(client=mock_client, cache=cache)

    with patch("fastdocparse.parser.extract_text_from_pdf", return_value="Total amount is 15.00 for this bill."):
        first = parser.extract(b"dummy-doc", schema)
        second = parser.extract(b"dummy-doc", schema)

    assert first == second
    mock_client.extract.assert_called_once()  # second call was served from cache
    assert len(cache) == 1


def test_cache_is_skipped_when_custom_rules_passed():
    schema = Schema(name="Test", fields=[Field(name="total", description="total")])
    mock_client = MagicMock()
    mock_client.extract.return_value = '{"total": "15.00"}'
    cache = InMemoryCache()
    parser = DocumentParser(client=mock_client, cache=cache)

    def noop_rule(extracted):
        return None

    with patch("fastdocparse.parser.extract_text_from_pdf", return_value="Total amount is 15.00 for this bill."):
        parser.extract(b"dummy-doc", schema, rules=[noop_rule])
        parser.extract(b"dummy-doc", schema, rules=[noop_rule])

    assert mock_client.extract.call_count == 2  # not cached, rules can't be fingerprinted
    assert len(cache) == 0


def _dummy_handler(document_bytes, structured_mode, config):
    return "dummy"


def _another_dummy_handler(document_bytes, structured_mode, config):
    return "dummy"


def test_cache_key_differs_by_schema():
    schema_a = Schema(name="A", fields=[Field(name="x", description="x")])
    schema_b = Schema(name="B", fields=[Field(name="y", description="y")])
    config = ExtractionConfig()
    key_a = make_cache_key(b"doc", schema_a, "pdf", config, _dummy_handler)
    key_b = make_cache_key(b"doc", schema_b, "pdf", config, _dummy_handler)
    assert key_a != key_b


def test_cache_key_differs_by_handler_identity():
    """Two DocumentParser instances can register different handlers under the same kind
    name — a shared cache must not conflate them (this was a real gap: the key used to
    be computed from kind alone)."""
    schema = Schema(name="A", fields=[Field(name="x", description="x")])
    config = ExtractionConfig()
    key_1 = make_cache_key(b"doc", schema, "docx", config, _dummy_handler)
    key_2 = make_cache_key(b"doc", schema, "docx", config, _another_dummy_handler)
    assert key_1 != key_2


def test_unknown_kind_raises_dedicated_error_not_bare_valueerror():
    from fastdocparse.parser import UnknownIngestionKindError

    schema = Schema(name="T", fields=[Field(name="x", description="x")])
    mock_client = MagicMock()
    parser = DocumentParser(client=mock_client)

    with pytest.raises(UnknownIngestionKindError):
        parser.extract(b"dummy", schema, kind="not_registered")


def test_concurrent_chunks_produce_same_result_as_sequential():
    schema = Schema(name="Multi", fields=[Field(name="items", type="list", description="items")])

    def fake_extract(prompt, chunk):
        return json.dumps({"items": [chunk]})

    chunks = ["chunk-a", "chunk-b", "chunk-c", "chunk-d"]

    mock_client_seq = MagicMock()
    mock_client_seq.extract.side_effect = fake_extract
    parser_seq = DocumentParser(client=mock_client_seq, config=ExtractionConfig(max_concurrent_chunks=1))

    mock_client_par = MagicMock()
    mock_client_par.extract.side_effect = fake_extract
    parser_par = DocumentParser(client=mock_client_par, config=ExtractionConfig(max_concurrent_chunks=4))

    with patch("fastdocparse.parser.chunk_document_text", return_value=chunks), \
         patch("fastdocparse.parser.extract_text_from_pdf", return_value="dummy dummy dummy dummy dummy dummy dummy dummy"), \
         patch("fastdocparse.parser.pymupdf.open"):
        result_seq = parser_seq.extract(b"dummy", schema)
        result_par = parser_par.extract(b"dummy", schema)

    assert result_seq["items"]["value"] == chunks
    assert result_par["items"]["value"] == chunks  # order preserved despite concurrency


def test_merge_prefers_chunk_grounded_value_over_hallucinated_first_chunk():
    schema = Schema(name="Grounded", fields=[Field(name="total", description="total", type="number")])
    mock_client = MagicMock()
    # Chunk 1 hallucinates a plausible-looking wrong total; chunk 2 has the real one,
    # grounded in its own chunk text.
    mock_client.extract.side_effect = ['{"total": 999.0}', '{"total": 100.0}']
    parser = DocumentParser(client=mock_client)

    with patch("fastdocparse.parser.chunk_document_text", return_value=["unrelated filler text", "Grand Total: 100.0"]), \
         patch("fastdocparse.parser.extract_text_from_pdf", return_value="dummy dummy dummy dummy dummy dummy dummy dummy"), \
         patch("fastdocparse.parser.pymupdf.open"):
        res = parser.extract(b"dummy", schema)

    assert res["total"]["value"] == 100.0


def test_instance_scoped_ingestion_handler_reachable_via_kind():
    schema = Schema(name="Custom", fields=[Field(name="x", description="x")])
    mock_client = MagicMock()
    mock_client.extract.return_value = '{"x": "hello from docx"}'
    parser = DocumentParser(client=mock_client)

    # Scoped to this instance only — no global mutation, nothing to clean up afterward.
    parser.register_ingestion_handler("docx", lambda document_bytes, structured_mode, config: "hello from docx source text")

    res = parser.extract(b"fake-docx-bytes", schema, kind="docx")

    assert res["x"]["value"] == "hello from docx"
    assert res["_meta"]["truncated"] is False  # non-pdf kinds skip page-count truncation


def test_instance_scoped_handler_does_not_leak_to_other_instances():
    schema = Schema(name="Custom", fields=[Field(name="x", description="x")])
    mock_client = MagicMock()
    parser_a = DocumentParser(client=mock_client)
    parser_b = DocumentParser(client=mock_client)

    parser_a.register_ingestion_handler("docx", lambda b, s, c: "from a")

    assert "docx" in parser_a._ingestion_handlers
    assert "docx" not in parser_b._ingestion_handlers
    with pytest.raises(ValueError):
        parser_b.extract(b"fake-docx-bytes", schema, kind="docx")


def test_global_register_only_affects_instances_created_after():
    schema = Schema(name="Custom", fields=[Field(name="x", description="x")])
    mock_client = MagicMock()
    mock_client.extract.return_value = '{"x": "global handler"}'
    existing_parser = DocumentParser(client=mock_client)

    register_default_ingestion_handler("xlsx_test_only", lambda b, s, c: "global handler source text")
    try:
        new_parser = DocumentParser(client=mock_client)
        res = new_parser.extract(b"fake-bytes", schema, kind="xlsx_test_only")
        assert res["x"]["value"] == "global handler"

        with pytest.raises(ValueError):
            existing_parser.extract(b"fake-bytes", schema, kind="xlsx_test_only")
    finally:
        INGESTION_HANDLERS.pop("xlsx_test_only", None)  # don't leak into other test modules


def test_invoice_schema_deduplicated_from_json_template():
    assert INVOICE_SCHEMA.name == "Invoice"
    field_names = {f.name for f in INVOICE_SCHEMA.fields}
    assert {"invoice_number", "total_price", "line_items", "exporter_name"} <= field_names
    assert INVOICE_SCHEMA.examples is not None and len(INVOICE_SCHEMA.examples) == 1


def test_in_memory_cache_evicts_oldest_when_over_capacity():
    cache = InMemoryCache(max_size=2)
    cache.set("a", {"v": 1})
    cache.set("b", {"v": 2})
    cache.set("c", {"v": 3})  # should evict "a"

    assert len(cache) == 2
    assert cache.get("a") is None
    assert cache.get("b") == {"v": 2}
    assert cache.get("c") == {"v": 3}


def test_in_memory_cache_get_refreshes_recency():
    cache = InMemoryCache(max_size=2)
    cache.set("a", {"v": 1})
    cache.set("b", {"v": 2})
    cache.get("a")  # touch "a" so "b" becomes the oldest
    cache.set("c", {"v": 3})  # should evict "b", not "a"

    assert cache.get("a") == {"v": 1}
    assert cache.get("b") is None
    assert cache.get("c") == {"v": 3}


def test_extraction_config_is_frozen():
    config = ExtractionConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.max_pages = 999


def test_extraction_result_from_raw_requires_meta():
    from fastdocparse.result import ExtractionResult

    with pytest.raises(ValueError):
        ExtractionResult.from_raw({"total": {"value": 1, "confidence": "high", "flags": []}})


def test_aextract_matches_sync_extract():
    schema = Schema(name="T", fields=[Field(name="total", description="total", type="number")])
    mock_client = MagicMock()
    mock_client.extract.return_value = '{"total": 100.0}'
    parser = DocumentParser(client=mock_client)

    with patch("fastdocparse.parser.extract_text_from_pdf", return_value="Grand Total: 100.0. Padding to skip OCR fallback."):
        sync_result = parser.extract(b"dummy", schema)
        async_result = asyncio.run(parser.aextract(b"dummy", schema))

    assert sync_result == async_result
