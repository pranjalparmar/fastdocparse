"""Adversarial edge cases found by deliberately trying to break the system.

Each test here reproduces a real bug found by hand — not a hypothetical — and pins the
fixed behavior down so it can't silently regress.
"""

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from fastdocparse.grounding import validate_field_constraints
from fastdocparse.json_repair import parse_json_from_llm
from fastdocparse.parser import DocumentParser, EmptyDocumentError
from fastdocparse.pdf_utils import chunk_document_text
from fastdocparse.schema import Field, Schema


def test_catastrophic_backtracking_pattern_does_not_hang():
    """A pathological user/LLM-supplied regex must not be able to hang extraction.
    Run in a subprocess with a hard wall-clock timeout — a naive thread-based guard
    looked like it worked but didn't (CPython's re engine holds the GIL during
    backtracking), so this test must actually observe wall-clock time, not just call
    the function and assume the timeout worked.

    The subprocess measures and prints its own internal elapsed time for just the
    validate_field_constraints() call, and that's what gets asserted on — not the
    subprocess's total wall time, which also includes interpreter startup and module
    imports. Under system load those alone can take several seconds, which would make
    a tight total-wall-time assertion flaky for reasons unrelated to whether the
    SIGALRM guard (a 1s timeout, see grounding.py) actually fired.
    """
    code = (
        "import time\n"
        "from fastdocparse.grounding import validate_field_constraints\n"
        "from fastdocparse.schema import Schema, Field\n"
        "schema = Schema(name='T', fields=[Field(name='x', description='x', pattern=r'^(a+)+$')])\n"
        "start = time.time()\n"
        "validate_field_constraints(schema, {'x': 'a' * 30 + '!'})\n"
        "print(f'ELAPSED={time.time() - start}')\n"
    )
    # Generous outer bound — this only needs to catch a genuine hang, not time the guard.
    result = subprocess.run([sys.executable, "-c", code], timeout=30, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    elapsed_line = next(line for line in result.stdout.splitlines() if line.startswith("ELAPSED="))
    elapsed = float(elapsed_line.split("=")[1])
    assert elapsed < 5, f"pattern check itself took {elapsed:.1f}s — timeout guard isn't working"


def test_required_field_rejects_empty_string():
    schema = Schema(name="T", fields=[Field(name="x", description="x", required=True)])
    issues = validate_field_constraints(schema, {"x": ""})
    assert any(i.kind == "missing_required" for i in issues)


def test_required_field_rejects_whitespace_only_string():
    schema = Schema(name="T", fields=[Field(name="x", description="x", required=True)])
    issues = validate_field_constraints(schema, {"x": "   "})
    assert any(i.kind == "missing_required" for i in issues)


def test_empty_string_value_not_trivially_grounded():
    """An empty string is a substring of everything — it must not count as "found"."""
    mock_client = MagicMock()
    mock_client.extract.return_value = '{"x": ""}'
    schema = Schema(name="T", fields=[Field(name="x", description="x", required=True)])
    parser = DocumentParser(client=mock_client)
    with patch("fastdocparse.parser.extract_text_from_pdf", return_value="padding padding padding padding padding"):
        res = parser.extract(b"dummy", schema)
    assert "grounded" not in res["x"]["flags"]
    assert res["x"]["value"] is None


def test_meta_reserved_field_name_rejected_at_schema_construction():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Schema(name="T", fields=[Field(name="_meta", description="reserved name")])


def test_oversized_single_page_gets_hard_split():
    """A page (or a document with no '--- PAGE' delimiters) can itself exceed the
    configured chunk budget — the between-pages splitter alone can't catch that."""
    huge_text = "word " * 50000  # ~250k chars, no page delimiters at all
    max_tokens = 3000
    chunks = chunk_document_text(huge_text, max_tokens=max_tokens)
    budget = max_tokens * 4
    assert len(chunks) > 1
    assert all(len(c) <= budget for c in chunks)


def test_corrupt_pdf_bytes_raise_clean_error_not_pymupdf_internals():
    """pymupdf.FileDataError/EmptyFileError are RuntimeError subclasses, not ValueError —
    they used to escape every exception handler in the library and the CLI."""
    schema = Schema(name="T", fields=[Field(name="x", description="x")])
    mock_client = MagicMock()
    parser = DocumentParser(client=mock_client)
    with pytest.raises(EmptyDocumentError):
        parser.extract(b"not a real pdf at all, just garbage bytes 12345", schema)


def test_empty_bytes_raise_clean_error():
    schema = Schema(name="T", fields=[Field(name="x", description="x")])
    mock_client = MagicMock()
    parser = DocumentParser(client=mock_client)
    with pytest.raises(EmptyDocumentError):
        parser.extract(b"", schema)


def test_truncated_json_recovers_partial_data():
    """A response cut off mid-object (hit max_tokens) should keep the fields that were
    fully generated before the cutoff, not discard the entire response."""
    truncated = '{"invoice_number": "INV-1", "line_items": [{"name": "Widget", "qty": 5'
    result = parse_json_from_llm(truncated)
    assert result.get("invoice_number") == "INV-1"
    assert result.get("line_items") == [{"name": "Widget", "qty": 5}]


def test_truncated_json_mid_key_drops_incomplete_fragment_cleanly():
    """Truncated exactly after a key with no value at all can't be closed into valid
    JSON by just appending brackets — the incomplete fragment must be dropped instead."""
    truncated = '{"invoice_number": "INV-1", "total_price":'
    result = parse_json_from_llm(truncated)
    assert result.get("invoice_number") == "INV-1"
    assert "total_price" not in result


def test_json_with_literal_braces_inside_string_values():
    raw = '{"description": "Use {curly} braces and {more} braces", "total": 100}'
    result = parse_json_from_llm(raw)
    assert result == {"description": "Use {curly} braces and {more} braces", "total": 100}
