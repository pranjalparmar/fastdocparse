"""Tests for compiling a plain-English description into a Schema."""

from unittest.mock import MagicMock

import pytest

from fastdocparse.llm_client import LLMClient
from fastdocparse.schema_compiler import compile_schema_from_description


def test_compile_schema_from_description_success():
    client = MagicMock(spec=LLMClient)
    client.complete.return_value = """
    {
      "name": "ShipmentManifest",
      "fields": [
        {"name": "bill_of_lading", "description": "B/L number", "type": "text", "required": true, "pattern": "BL-\\\\d{4,}", "enum": null, "sub_fields": null},
        {"name": "shipment_status", "description": "Status", "type": "text", "required": false, "pattern": null, "enum": ["in_transit", "delivered"], "sub_fields": null}
      ]
    }
    """

    schema = compile_schema_from_description(
        "I want the bill of lading number (starts with BL-) and shipment status (in_transit or delivered).",
        client,
    )

    assert schema.name == "ShipmentManifest"
    field_names = [f.name for f in schema.fields]
    assert field_names == ["bill_of_lading", "shipment_status"]
    assert schema.get_field("bill_of_lading").required is True
    assert schema.get_field("bill_of_lading").pattern == r"BL-\d{4,}"
    assert schema.get_field("shipment_status").enum == ["in_transit", "delivered"]

    client.complete.assert_called_once()


def test_compile_schema_from_description_handles_markdown_fenced_response():
    client = MagicMock(spec=LLMClient)
    client.complete.return_value = '```json\n{"name": "Invoice", "fields": [{"name": "total", "description": "Total"}]}\n```'

    schema = compile_schema_from_description("total amount", client)
    assert schema.name == "Invoice"
    assert schema.fields[0].name == "total"


def test_compile_schema_from_description_raises_on_empty_fields():
    client = MagicMock(spec=LLMClient)
    client.complete.return_value = '{"name": "Empty", "fields": []}'

    with pytest.raises(ValueError):
        compile_schema_from_description("vague request", client)


def test_compile_schema_from_description_raises_on_unparseable_response():
    client = MagicMock(spec=LLMClient)
    client.complete.return_value = "I refuse to produce JSON today."

    with pytest.raises(ValueError):
        compile_schema_from_description("something", client)
