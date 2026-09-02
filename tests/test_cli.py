"""Tests for the CLI entrypoint."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from fastdocparse import __version__
from fastdocparse.cli import app

runner = CliRunner()
REPO_ROOT = Path(__file__).parent.parent
SAMPLE_IMAGE = REPO_ROOT / "sample_invoice.png"
INVOICE_SCHEMA_PATH = REPO_ROOT / "src" / "fastdocparse" / "schemas" / "invoice.json"


def _mock_openai_returning(content: str):
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices = [MagicMock(message=MagicMock(content=content))]
    return mock_client


def test_extract_command_success():
    fake_result = json.dumps({
        "invoice_number": "INV-1", "invoice_date": None, "exporter_name": None,
        "exporter_address": None, "importer_name": None, "importer_address": None,
        "currency": None, "total_price": None, "line_items": [],
    })
    with patch("fastdocparse.llm_client.OpenAI", return_value=_mock_openai_returning(fake_result)):
        result = runner.invoke(app, ["extract", str(SAMPLE_IMAGE), str(INVOICE_SCHEMA_PATH)])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["invoice_number"]["value"] == "INV-1"
    assert "_meta" in payload


def test_version_flag_prints_package_version():
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"fastdocparse {__version__}"


def test_extract_command_rejects_unsupported_extension(tmp_path):
    bad_file = tmp_path / "resume.docx"
    bad_file.write_text("not a real document")

    result = runner.invoke(app, ["extract", str(bad_file), str(INVOICE_SCHEMA_PATH)])

    assert result.exit_code == 1
    assert "Unsupported file type" in result.output


def test_extract_command_reports_bad_schema_cleanly(tmp_path):
    bad_schema = tmp_path / "bad_schema.json"
    bad_schema.write_text('{"name": "Bad", "fields": "not a list"}')

    result = runner.invoke(app, ["extract", str(SAMPLE_IMAGE), str(bad_schema)])

    assert result.exit_code == 1
    assert "Could not load schema" in result.output


def test_extract_command_writes_output_file(tmp_path):
    fake_result = json.dumps({
        "invoice_number": "INV-1", "invoice_date": None, "exporter_name": None,
        "exporter_address": None, "importer_name": None, "importer_address": None,
        "currency": None, "total_price": None, "line_items": [],
    })
    output_path = tmp_path / "result.json"
    with patch("fastdocparse.llm_client.OpenAI", return_value=_mock_openai_returning(fake_result)):
        result = runner.invoke(app, ["extract", str(SAMPLE_IMAGE), str(INVOICE_SCHEMA_PATH), "--output", str(output_path)])

    assert result.exit_code == 0
    assert output_path.exists()
    assert json.loads(output_path.read_text())["invoice_number"]["value"] == "INV-1"


def test_schema_from_text_success(tmp_path):
    fake_schema = json.dumps({
        "name": "ShipmentManifest",
        "fields": [{"name": "bill_of_lading", "description": "B/L number", "required": True}],
    })
    output_path = tmp_path / "generated.json"
    with patch("fastdocparse.llm_client.OpenAI", return_value=_mock_openai_returning(fake_schema)):
        result = runner.invoke(app, ["schema-from-text", "the bill of lading number", "--output", str(output_path)])

    assert result.exit_code == 0
    assert output_path.exists()
    saved = json.loads(output_path.read_text())
    assert saved["name"] == "ShipmentManifest"


def test_extract_command_creates_missing_output_directory(tmp_path):
    fake_result = json.dumps({
        "invoice_number": "INV-1", "invoice_date": None, "exporter_name": None,
        "exporter_address": None, "importer_name": None, "importer_address": None,
        "currency": None, "total_price": None, "line_items": [],
    })
    output_path = tmp_path / "nested" / "dir" / "result.json"
    with patch("fastdocparse.llm_client.OpenAI", return_value=_mock_openai_returning(fake_result)):
        result = runner.invoke(app, ["extract", str(SAMPLE_IMAGE), str(INVOICE_SCHEMA_PATH), "--output", str(output_path)])

    assert result.exit_code == 0
    assert output_path.exists()


def test_extract_command_rejects_unregistered_kind_cleanly():
    result = runner.invoke(app, ["extract", str(SAMPLE_IMAGE), str(INVOICE_SCHEMA_PATH), "--kind", "docx"])

    assert result.exit_code == 1
    assert "No ingestion handler registered" in result.output


def test_schema_from_text_creates_missing_output_directory(tmp_path):
    fake_schema = json.dumps({
        "name": "ShipmentManifest",
        "fields": [{"name": "bill_of_lading", "description": "B/L number", "required": True}],
    })
    output_path = tmp_path / "nested" / "dir" / "generated.json"
    with patch("fastdocparse.llm_client.OpenAI", return_value=_mock_openai_returning(fake_schema)):
        result = runner.invoke(app, ["schema-from-text", "the bill of lading number", "--output", str(output_path)])

    assert result.exit_code == 0
    assert output_path.exists()


def test_schema_from_text_reports_generation_failure_cleanly(tmp_path):
    output_path = tmp_path / "generated.json"
    with patch("fastdocparse.llm_client.OpenAI", return_value=_mock_openai_returning("not json at all")):
        result = runner.invoke(app, ["schema-from-text", "vague request", "--output", str(output_path)])

    assert result.exit_code == 1
    assert "Could not generate a schema" in result.output
    assert not output_path.exists()


def test_extract_command_missing_schema_shows_friendly_message():
    """When schema is omitted, the user should see how to create one — not Typer's generic error."""
    result = runner.invoke(app, ["extract", str(SAMPLE_IMAGE)])

    assert result.exit_code == 1
    assert "schema file is required" in result.output
    assert "schema-from-text" in result.output
    assert "list-schemas" in result.output


def test_list_schemas_shows_bundled_schemas():
    """list-schemas should enumerate at least the invoice.json bundled example."""
    result = runner.invoke(app, ["list-schemas"])

    assert result.exit_code == 0
    assert "invoice" in result.output.lower()


def test_extract_command_rejects_unreadable_schema_cleanly(tmp_path):
    """A schema that exists but can't be read (permission denied) must fail with a clean
    Typer-level error, not skip straight past the friendly-missing-schema path and crash
    later with a raw OSError when the code tries to actually open it."""
    unreadable_schema = tmp_path / "no_access.json"
    unreadable_schema.write_text('{"name": "T", "fields": []}')
    unreadable_schema.chmod(0o000)
    try:
        result = runner.invoke(app, ["extract", str(SAMPLE_IMAGE), str(unreadable_schema)])
    finally:
        unreadable_schema.chmod(0o644)

    assert result.exit_code != 0
    assert "readable" in result.output.lower()
