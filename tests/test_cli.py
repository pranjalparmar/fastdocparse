"""Tests for the CLI entrypoint."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from fastdocparse import __version__
from fastdocparse.cli import app
from fastdocparse.config import ExtractionConfig

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
        result = runner.invoke(app, ["extract", str(SAMPLE_IMAGE), str(INVOICE_SCHEMA_PATH), "--api-key", "test-key"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["invoice_number"]["value"] == "INV-1"
    assert "_meta" in payload


def test_extract_command_no_credentials_shows_friendly_message():
    """No --api-key, no --base-url, and no relevant env vars set: should fail fast with
    setup guidance instead of reaching the network and getting an OpenAI auth error."""
    result = runner.invoke(
        app,
        ["extract", str(SAMPLE_IMAGE), str(INVOICE_SCHEMA_PATH)],
        env={"LLM_API_KEY": "", "OPENAI_API_KEY": "", "FASTDOCPARSE_BASE_URL": ""},
    )

    assert result.exit_code == 1
    assert "no LLM credentials configured" in result.output
    assert "OPENAI_API_KEY" in result.output
    assert "--base-url" in result.output


def test_extract_command_treats_blank_env_var_as_absent():
    """A blank/empty env var (e.g. LLM_API_KEY="") must still trigger the friendly
    credentials message, not silently bypass it into LLMClient's dummy-key fallback."""
    result = runner.invoke(
        app,
        ["extract", str(SAMPLE_IMAGE), str(INVOICE_SCHEMA_PATH)],
        env={"LLM_API_KEY": "   ", "OPENAI_API_KEY": "", "FASTDOCPARSE_BASE_URL": ""},
    )

    assert result.exit_code == 1
    assert "no LLM credentials configured" in result.output


def test_extract_command_accepts_openai_api_key_env_var():
    """README documents `export OPENAI_API_KEY=...` as valid setup; the --api-key
    option must actually read it, not just the fastdocparse-specific LLM_API_KEY."""
    fake_result = json.dumps({
        "invoice_number": "INV-1", "invoice_date": None, "exporter_name": None,
        "exporter_address": None, "importer_name": None, "importer_address": None,
        "currency": None, "total_price": None, "line_items": [],
    })
    with patch("fastdocparse.llm_client.OpenAI", return_value=_mock_openai_returning(fake_result)):
        result = runner.invoke(
            app,
            ["extract", str(SAMPLE_IMAGE), str(INVOICE_SCHEMA_PATH)],
            env={"OPENAI_API_KEY": "sk-from-env"},
        )

    assert result.exit_code == 0


def test_extract_command_accepts_model_and_base_url_env_vars():
    """FASTDOCPARSE_MODEL / FASTDOCPARSE_BASE_URL should work as flag-free config for
    local-model users who don't want to repeat --model/--base-url on every command."""
    fake_result = json.dumps({
        "invoice_number": "INV-1", "invoice_date": None, "exporter_name": None,
        "exporter_address": None, "importer_name": None, "importer_address": None,
        "currency": None, "total_price": None, "line_items": [],
    })
    with patch("fastdocparse.llm_client.OpenAI", return_value=_mock_openai_returning(fake_result)) as mock_openai:
        result = runner.invoke(
            app,
            ["extract", str(SAMPLE_IMAGE), str(INVOICE_SCHEMA_PATH)],
            env={
                "FASTDOCPARSE_MODEL": "llama3.2",
                "FASTDOCPARSE_BASE_URL": "http://localhost:11434/v1",
                "LLM_API_KEY": "ollama",
            },
        )

    assert result.exit_code == 0
    mock_openai.assert_called_once_with(base_url="http://localhost:11434/v1", api_key="ollama", timeout=60.0)
    assert mock_openai.return_value.chat.completions.create.call_args.kwargs["model"] == "llama3.2"


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

    result = runner.invoke(app, ["extract", str(SAMPLE_IMAGE), str(bad_schema), "--api-key", "test-key"])

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
        result = runner.invoke(app, ["extract", str(SAMPLE_IMAGE), str(INVOICE_SCHEMA_PATH), "--output", str(output_path), "--api-key", "test-key"])

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
        result = runner.invoke(app, ["schema-from-text", "the bill of lading number", "--output", str(output_path), "--api-key", "test-key"])

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
        result = runner.invoke(app, ["extract", str(SAMPLE_IMAGE), str(INVOICE_SCHEMA_PATH), "--output", str(output_path), "--api-key", "test-key"])

    assert result.exit_code == 0
    assert output_path.exists()


def test_extract_command_rejects_unregistered_kind_cleanly():
    result = runner.invoke(app, ["extract", str(SAMPLE_IMAGE), str(INVOICE_SCHEMA_PATH), "--kind", "docx", "--api-key", "test-key"])

    assert result.exit_code == 1
    assert "No ingestion handler registered" in result.output


def test_extract_command_passes_max_pages_to_document_parser():
    fake_result = {
        "_meta": {"truncated": False, "truncation_reason": None},
        "invoice_number": {"value": "INV-1", "confidence": "high", "flags": ["grounded"]},
    }

    with patch("fastdocparse.cli.DocumentParser") as parser_cls, patch("fastdocparse.cli.LLMClient"):
        parser = parser_cls.return_value
        parser.extract.return_value = fake_result

        result = runner.invoke(app, ["extract", str(SAMPLE_IMAGE), str(INVOICE_SCHEMA_PATH), "--max-pages", "5", "--api-key", "test-key"])

    assert result.exit_code == 0
    _, kwargs = parser_cls.call_args
    assert isinstance(kwargs["config"], ExtractionConfig)
    assert kwargs["config"].max_pages == 5


def test_extract_command_reports_invalid_max_pages_cleanly():
    result = runner.invoke(app, ["extract", str(SAMPLE_IMAGE), str(INVOICE_SCHEMA_PATH), "--max-pages", "0", "--api-key", "test-key"])

    assert result.exit_code == 1
    assert "max_pages must be positive" in result.output
    assert "Traceback" not in result.output


def test_schema_from_text_creates_missing_output_directory(tmp_path):
    fake_schema = json.dumps({
        "name": "ShipmentManifest",
        "fields": [{"name": "bill_of_lading", "description": "B/L number", "required": True}],
    })
    output_path = tmp_path / "nested" / "dir" / "generated.json"
    with patch("fastdocparse.llm_client.OpenAI", return_value=_mock_openai_returning(fake_schema)):
        result = runner.invoke(app, ["schema-from-text", "the bill of lading number", "--output", str(output_path), "--api-key", "test-key"])

    assert result.exit_code == 0
    assert output_path.exists()


def test_schema_from_text_reports_generation_failure_cleanly(tmp_path):
    output_path = tmp_path / "generated.json"
    with patch("fastdocparse.llm_client.OpenAI", return_value=_mock_openai_returning("not json at all")):
        result = runner.invoke(app, ["schema-from-text", "vague request", "--output", str(output_path), "--api-key", "test-key"])

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


def test_validate_schema_valid_bundled():
    """validate-schema should successfully validate a bundled schema and report field count."""
    result = runner.invoke(app, ["validate-schema", str(INVOICE_SCHEMA_PATH)])

    assert result.exit_code == 0
    assert "Schema 'Invoice' is valid" in result.output
    assert "field(s)" in result.output


def test_validate_schema_invalid_reserved_field(tmp_path):
    """validate-schema should reject schemas using reserved field names like _meta."""
    bad_schema = tmp_path / "bad_schema.json"
    bad_schema.write_text(json.dumps({
        "name": "Bad",
        "fields": [{"name": "_meta", "description": "reserved"}],
    }))

    result = runner.invoke(app, ["validate-schema", str(bad_schema)])

    assert result.exit_code == 1
    assert "Could not load schema" in result.output
    assert "_meta" in result.output


def test_validate_schema_invalid_json(tmp_path):
    """validate-schema should report clear error for malformed json."""
    broken_file = tmp_path / "broken.json"
    broken_file.write_text("{not-valid-json")

    result = runner.invoke(app, ["validate-schema", str(broken_file)])

    assert result.exit_code == 1
    assert "Could not load schema" in result.output


def test_validate_schema_valid_yaml(tmp_path):
    """validate-schema should accept .yaml/.yml, not just .json (credit: PR #59)."""
    yaml_schema = tmp_path / "custom_schema.yaml"
    yaml_schema.write_text(
        "name: YamlSchema\n"
        "fields:\n"
        "  - name: title\n"
        "    description: Document title\n"
    )

    result = runner.invoke(app, ["validate-schema", str(yaml_schema)])

    assert result.exit_code == 0
    assert "Schema 'YamlSchema' is valid" in result.output


def test_validate_schema_rejects_unsupported_extension(tmp_path):
    """validate-schema should report a clear error for an unsupported file extension,
    not just a generic "could not load" with no reason (credit: PR #59)."""
    txt_schema = tmp_path / "schema.txt"
    txt_schema.write_text("{}")

    result = runner.invoke(app, ["validate-schema", str(txt_schema)])

    assert result.exit_code == 1
    assert "Could not load schema" in result.output
    assert "Unsupported schema file extension" in result.output


def test_extract_command_rejects_unreadable_schema_cleanly(tmp_path, monkeypatch):
    """A schema that exists but can't be read (permission denied) must fail with a clean
    Typer-level error, not skip straight past the friendly-missing-schema path and crash
    later with a raw OSError when the code tries to actually open it.

    `os.access` is patched rather than the file's mode being changed: `chmod(0o000)`
    doesn't remove the owner's read access on Windows (the permission model there is
    ACL-based, and `chmod` only maps to the read-only attribute), so `os.access(...,
    R_OK)`, which is what Typer's `readable=True` actually calls, still answered True
    and the guard under test never fired. Patching the call directly exercises the same
    code path identically on every platform, credit: PR #64 (dchaudhari7177)."""
    unreadable_schema = tmp_path / "no_access.json"
    unreadable_schema.write_text('{"name": "T", "fields": []}')

    real_access = os.access

    def deny_reading_the_schema(path, mode, *args, **kwargs):
        if Path(path) == unreadable_schema and mode & os.R_OK:
            return False
        return real_access(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "access", deny_reading_the_schema)

    result = runner.invoke(app, ["extract", str(SAMPLE_IMAGE), str(unreadable_schema)])

    assert result.exit_code != 0
    assert "readable" in result.output.lower()
