"""Tests for the generalized document extractor Phase 1 & 2."""

import pytest
from unittest.mock import MagicMock, patch

from fastdocparse.schema import Schema, Field
from fastdocparse.example_schemas import INVOICE_SCHEMA
from fastdocparse.llm_client import LLMClient
from fastdocparse.parser import DocumentParser, _parse_json_from_llm
from fastdocparse.grounding import Issue


def test_tc1_1_invoice_schema_extraction():
    """TC1.1 - Extract with standard invoice schema."""
    schema = Schema(
        name="Invoice",
        fields=[
            Field(name="invoice_number", description="The invoice number"),
            Field(name="total_price", description="Total amount", type="number"),
        ]
    )
    
    mock_client = MagicMock()
    # Return a perfect JSON
    mock_client.extract.return_value = '```json\n{"invoice_number": "INV-123", "total_price": 100.0}\n```'
    
    parser = DocumentParser(client=mock_client)
    
    with patch("fastdocparse.parser.extract_text_from_pdf", return_value="Invoice INV-123 Total 100.0. This is a very long text to avoid OCR fallback."):
        res = parser.extract(b"dummy_pdf_bytes", schema)
        
    assert res["invoice_number"]["value"] == "INV-123"
    assert res["total_price"]["value"] == 100.0


def test_tc1_2_arbitrary_schema():
    """TC1.2 - Extract with different, arbitrary schema."""
    schema = Schema(
        name="Arbitrary",
        fields=[
            Field(name="weather", description="Current weather"),
            Field(name="mood", description="Current mood"),
        ]
    )
    mock_client = MagicMock()
    mock_client.extract.return_value = '{"weather": "sunny", "mood": "happy"}'
    parser = DocumentParser(client=mock_client)
    
    with patch("fastdocparse.parser.extract_text_from_pdf", return_value="It is sunny and I am happy today and everything is great"):
        res = parser.extract(b"dummy", schema)
        
    assert "weather" in res
    assert "mood" in res
    assert "invoice_number" not in res
    
def test_tc1_3_scanned_receipt():
    """TC1.3 - Scanned receipt OCR path."""
    schema = Schema(name="Receipt", fields=[Field(name="total", description="total")])
    mock_client = MagicMock()
    mock_client.extract.return_value = '{"total": "15.00"}'
    parser = DocumentParser(client=mock_client)
    
    with patch("fastdocparse.parser.extract_text_from_image_ocr", return_value="Total: 15.00") as mock_ocr:
        res = parser.extract(b"dummy_img", schema, is_image=True)
        
    mock_ocr.assert_called_once()
    assert res["total"]["value"] == "15.00"

def test_tc1_4_different_endpoints():
    """TC1.4 - Point model= at two different endpoints."""
    # This just tests our client wrapper takes different base URLs
    client_openai = LLMClient(model="gpt-4o")
    client_ollama = LLMClient(base_url="http://localhost:11434/v1", model="llama3")
    
    assert str(client_openai.client.base_url) != str(client_ollama.client.base_url)

def test_tc1_5_malformed_llm_response():
    """TC1.5 - Feed a deliberately malformed LLM response."""
    malformed_response = """
    <think>
    I am reasoning about this document...
    It seems to be an invoice.
    </think>
    Here is the requested JSON:
    ```json
    {
        "invoice_number": "999"
    }
    ```
    Have a nice day!
    """
    parsed = _parse_json_from_llm(malformed_response)
    assert parsed.get("invoice_number") == "999"

def test_tc1_6_missing_field():
    """TC1.6 - Feed document missing a field; confirm comes back null."""
    schema = Schema(
        name="MissingField",
        fields=[
            Field(name="found_field", description="Found"),
            Field(name="missing_field", description="Missing"),
        ]
    )
    mock_client = MagicMock()
    mock_client.extract.return_value = '{"found_field": "yes"}'
    parser = DocumentParser(client=mock_client)
    
    with patch("fastdocparse.parser.extract_text_from_pdf", return_value="dummy dummy dummy dummy dummy dummy dummy dummy yes"):
        res = parser.extract(b"dummy", schema)
        
    assert res["found_field"]["value"] == "yes"
    assert res["missing_field"]["value"] is None

def test_tc2_1_grounded_value():
    """TC2.1 - Extract a field whose value appears verbatim in the source text."""
    schema = Schema(name="Test", fields=[Field(name="total", description="total")])
    mock_client = MagicMock()
    mock_client.extract.return_value = '{"total": "15.00"}'
    parser = DocumentParser(client=mock_client)
    
    with patch("fastdocparse.parser.extract_text_from_pdf", return_value="Total amount is 15.00 for this bill."):
        res = parser.extract(b"dummy", schema)
        
    assert res["total"]["value"] == "15.00"
    assert res["total"]["confidence"] == "high"
    assert "grounded" in res["total"]["flags"]

def test_tc2_2_ungrounded_value():
    """TC2.2 - Fabricated value not present in source text."""
    schema = Schema(name="Test", fields=[Field(name="total", description="total")])
    mock_client = MagicMock()
    mock_client.extract.return_value = '{"total": "99.99"}'
    parser = DocumentParser(client=mock_client)
    
    with patch("fastdocparse.parser.extract_text_from_pdf", return_value="Total amount is 15.00 for this bill."):
        res = parser.extract(b"dummy", schema)
        
    assert res["total"]["value"] == "99.99"
    assert res["total"]["confidence"] == "low"
    assert "ungrounded" in res["total"]["flags"]
    assert "grounded" not in res["total"]["flags"]

def test_tc2_3_cross_check_failure():
    """TC2.3 - Line items sum mismatch flags both fields."""
    schema = Schema(name="Test", fields=[
        Field(name="total", description="total", type="number"),
        Field(name="line_items", description="lines", type="list")
    ])
    mock_client = MagicMock()
    mock_client.extract.return_value = '{"total": 100.0, "line_items": [{"amount": 20.0}, {"amount": 50.0}]}'
    parser = DocumentParser(client=mock_client)
    
    def sum_check(extracted):
        total = extracted.get("total")
        lines = extracted.get("line_items", [])
        if total is not None and lines:
            calc_total = sum(item.get("amount", 0) for item in lines)
            if calc_total != total:
                return [Issue(field="total", message="Mismatch"), Issue(field="line_items", message="Mismatch")]
        return None
    
    with patch("fastdocparse.parser.extract_text_from_pdf", return_value="total 100.0 items 20.0 50.0 and here is some extra padding text"):
        res = parser.extract(b"dummy", schema, rules=[sum_check])
        
    assert "failed_check" in res["total"]["flags"]
    assert "failed_check" in res["line_items"]["flags"]

def test_tc2_4_unparseable_date_flagged():
    """TC2.4 - Date field unparseable flagged by rule."""
    schema = Schema(name="Test", fields=[Field(name="date", description="date")])
    mock_client = MagicMock()
    mock_client.extract.return_value = '{"date": "Not a date"}'
    parser = DocumentParser(client=mock_client)
    
    def date_check(extracted):
        val = extracted.get("date")
        if val == "Not a date":
            return [Issue(field="date", message="Invalid date")]
        return None
        
    with patch("fastdocparse.parser.extract_text_from_pdf", return_value="date Not a date. Here is some more text to make it longer."):
        res = parser.extract(b"dummy", schema, rules=[date_check])
        
    assert "failed_check" in res["date"]["flags"]

def test_tc2_5_no_extra_llm_calls():
    """TC2.5 - Confirm Phase 2 adds no additional LLM API calls."""
    schema = Schema(name="Test", fields=[Field(name="total", description="total")])
    mock_client = MagicMock()
    mock_client.extract.return_value = '{"total": "15.00"}'
    parser = DocumentParser(client=mock_client)
    
    with patch("fastdocparse.parser.extract_text_from_pdf", return_value="Total amount is 15.00 for this bill."):
        parser.extract(b"dummy", schema)

    mock_client.extract.assert_called_once()

def test_tc3_1_schema_examples_compile():
    """TC3.1 - Prompt compiler includes examples if present and omits if not."""
    from fastdocparse.prompt_compiler import compile_prompt
    
    # 0 examples
    schema_0 = Schema(name="NoExamples", fields=[Field(name="test", description="test field")])
    prompt_0 = compile_prompt(schema_0)
    assert "STEP 3 — EXAMPLES" not in prompt_0
    assert "STEP 3 — JSON OUTPUT" in prompt_0
    
    # with examples
    schema_ex = Schema(
        name="WithExamples", 
        fields=[Field(name="test", description="test field")],
        examples=[("Doc text", {"test": "val"})]
    )
    prompt_ex = compile_prompt(schema_ex)
    assert "STEP 3 — EXAMPLES" in prompt_ex
    assert "STEP 4 — JSON OUTPUT" in prompt_ex
    assert "Doc text" in prompt_ex
    assert "val" in prompt_ex

def test_tc3_2_invoice_schema():
    """TC3.2 - Confirm invoice schema with examples is valid and parsable."""
    mock_client = MagicMock()
    # Mock returning something that looks like the expected schema
    mock_client.extract.return_value = '{"invoice_number": "INV-123", "total_price": 100.0, "exporter_name": "Acme"}'
    parser = DocumentParser(client=mock_client)
    
    with patch("fastdocparse.parser.extract_text_from_pdf", return_value="Acme Corp INV-123 100.0. Added text to avoid OCR fallback."):
        res = parser.extract(b"dummy", INVOICE_SCHEMA)
        
    assert res["invoice_number"]["value"] == "INV-123"
    assert res["total_price"]["value"] == 100.0
    assert res["exporter_name"]["value"] == "Acme"

def test_tc3_3_malformed_examples():
    """TC3.3 - Confirm malformed examples raise a validation error."""
    from pydantic import ValidationError
    
    with pytest.raises(ValidationError):
        Schema(
            name="Bad",
            fields=[Field(name="f", description="d")],
            examples=["This is just a string, not a tuple!"]
        )
    
    with pytest.raises(ValidationError):
        Schema(
            name="Bad2",
            fields=[Field(name="f", description="d")],
            examples=[("String", "Also a string instead of dict")]
        )

def test_tc4_1_structured_mode_enabled():
    """TC4.1 - Verify structured_mode is automatically enabled for lists."""
    schema = Schema(name="HasList", fields=[Field(name="items", type="list", description="A list")])
    mock_client = MagicMock()
    mock_client.extract.return_value = '{"items": []}'
    parser = DocumentParser(client=mock_client)
    
    with patch("fastdocparse.parser.extract_text_from_pdf", return_value="dummy dummy dummy dummy dummy dummy dummy dummy") as mock_extract:
        parser.extract(b"dummy", schema)
        
    mock_extract.assert_called_once_with(b"dummy", max_pages=15, structured_mode=True)

def test_tc4_2_structured_mode_disabled():
    """TC4.2 - Confirm non-table schema runs without structured_mode."""
    schema = Schema(name="NoList", fields=[Field(name="text", type="text", description="A text")])
    mock_client = MagicMock()
    mock_client.extract.return_value = '{"text": "val"}'
    parser = DocumentParser(client=mock_client)
    
    with patch("fastdocparse.parser.extract_text_from_pdf", return_value="dummy dummy dummy dummy dummy dummy dummy dummy") as mock_extract:
        parser.extract(b"dummy", schema)
        
    mock_extract.assert_called_once_with(b"dummy", max_pages=15, structured_mode=False)

def test_tc4_3_ocr_structured_formatting():
    """TC4.3 - Verify OCR engine formats output with X coordinates in structured_mode."""
    from fastdocparse.ocr_engine import extract_text_from_image_ocr, HAS_RAPID_OCR
    if not HAS_RAPID_OCR:
        pytest.skip("RapidOCR not installed")
        
    # We mock the rapidocr return value to simulate an image containing two columns
    with patch("fastdocparse.ocr_engine._rapid_ocr") as mock_ocr, patch("fastdocparse.ocr_engine.Image.open"):
        # result is a tuple (list_of_boxes, _)
        # Each box format: ([ [x0,y0], [x1,y1], [x2,y2], [x3,y3] ], text, confidence)
        mock_ocr.return_value = (
            [
                ([[10.0, 10.0], [20.0, 10.0], [20.0, 20.0], [10.0, 20.0]], "Col1", 0.9),
                ([[100.0, 10.0], [120.0, 10.0], [120.0, 20.0], [100.0, 20.0]], "Col2", 0.9),
            ],
            None
        )
        
        # Test standard mode
        text_standard = extract_text_from_image_ocr(b"dummy", structured_mode=False)
        assert "Col1    Col2" in text_standard
        assert "[X:" not in text_standard
        
        # Test structured mode
        text_structured = extract_text_from_image_ocr(b"dummy", structured_mode=True)
        assert "[X:10] Col1" in text_structured
        assert "[X:100] Col2" in text_structured


def test_tc5_1_list_chunking():
    """TC5.1 - Test list-type field extraction across chunks."""
    schema = Schema(name="ChunkList", fields=[Field(name="items", type="list", description="A list")])
    mock_client = MagicMock()
    mock_client.extract.side_effect = [
        '{"items": ["item1"]}',
        '{"items": ["item2"]}'
    ]
    parser = DocumentParser(client=mock_client)
    
    with patch("fastdocparse.parser.chunk_document_text", return_value=["chunk1", "chunk2"]), \
         patch("fastdocparse.parser.extract_text_from_pdf", return_value="dummy dummy dummy dummy dummy dummy dummy dummy"), \
         patch("fastdocparse.parser.pymupdf.open"):
        res = parser.extract(b"dummy", schema)
        
    assert res["items"]["value"] == ["item1", "item2"]
    assert res["_meta"]["truncated"] is False

def test_tc5_2_single_value_chunking():
    """TC5.2 - Test single-value field found in a later chunk."""
    schema = Schema(name="ChunkSingle", fields=[Field(name="total", type="number", description="total")])
    mock_client = MagicMock()
    mock_client.extract.side_effect = [
        '{"total": null}',
        '{"total": 100}'
    ]
    parser = DocumentParser(client=mock_client)
    
    with patch("fastdocparse.parser.chunk_document_text", return_value=["chunk1", "chunk2"]), \
         patch("fastdocparse.parser.extract_text_from_pdf", return_value="dummy dummy dummy dummy dummy dummy dummy dummy"), \
         patch("fastdocparse.parser.pymupdf.open"):
        res = parser.extract(b"dummy", schema)
        
    assert res["total"]["value"] == 100

def test_tc5_3_truncation_flag():
    """TC5.3 - Test truncated flag on exceeding hard limit."""
    schema = Schema(name="ChunkLimit", fields=[Field(name="test", type="text", description="test")])
    mock_client = MagicMock()
    mock_client.extract.return_value = '{"test": "val"}'
    parser = DocumentParser(client=mock_client)
    
    mock_doc = MagicMock()
    mock_doc.__len__.return_value = 20
    
    with patch("fastdocparse.parser.pymupdf.open", return_value=mock_doc), \
         patch("fastdocparse.parser.extract_text_from_pdf", return_value="dummy dummy dummy dummy dummy dummy dummy dummy"), \
         patch("fastdocparse.parser.chunk_document_text", return_value=["chunk1"]):
        res = parser.extract(b"dummy", schema)
        
    assert res["_meta"]["truncated"] is True
    assert "20 pages" in res["_meta"]["truncation_reason"]


