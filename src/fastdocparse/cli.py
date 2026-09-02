"""Command-line entrypoint: extract fields from a document without writing code.

Usage:
    fastdocparse extract document.pdf invoice_schema.json
    fastdocparse extract receipt.jpg shipment_schema.json --model llama3 --base-url http://localhost:11434/v1
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import typer
from pydantic import ValidationError

from . import __version__
from .llm_client import LLMClient, LLMClientError
from .parser import DocumentParser, EmptyDocumentError, UnknownIngestionKindError
from .schema import Schema
from .schema_compiler import compile_schema_from_description


def _load_plugins() -> None:
    """Import modules listed in FASTDOCPARSE_PLUGINS (comma-separated) so they can call
    parser.register_default_ingestion_handler() on import — the only way a custom
    ingestion kind (DOCX, XLSX, ...) becomes reachable from this CLI, since a fresh CLI
    process otherwise only knows the built-in "pdf"/"image" handlers.

    Security note: this imports and runs arbitrary Python from wherever FASTDOCPARSE_PLUGINS
    points, at CLI startup, with no sandboxing — the same trust model as PYTHONSTARTUP or
    DJANGO_SETTINGS_MODULE. That's fine for a user pointing it at their own plugin on their
    own machine, which is the only supported use. Never let this env var be set from an
    untrusted source (e.g. a request parameter in a hosted service built on this CLI).
    """
    plugin_spec = os.environ.get("FASTDOCPARSE_PLUGINS", "")
    for module_name in filter(None, (p.strip() for p in plugin_spec.split(","))):
        importlib.import_module(module_name)


_load_plugins()

app = typer.Typer(
    add_completion=False,
    help="Extract structured data from a PDF/PNG/JPG using a schema file you define — no coding required.",
)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | {".pdf"}


def _check_llm_credentials(base_url: str | None, api_key: str | None) -> None:
    """Catch the common "nothing configured" case before making any network call, rather
    than letting it surface as an OpenAI auth error after a request round-trip. A
    `base_url` (local/self-hosted endpoint) needs no real key, so this only fires when
    neither a base_url nor a key is present from any source (flag or env var). A blank
    or whitespace-only value (e.g. an env var set to "") counts as absent, not present,
    since otherwise it would silently reach LLMClient's own dummy-key fallback and
    proceed to a doomed network call instead of failing here with a clear message."""
    if (base_url and base_url.strip()) or (api_key and api_key.strip()):
        return

    typer.echo(
        "Error: no LLM credentials configured.\n"
        "\n"
        "To use OpenAI, set an API key:\n"
        "  export OPENAI_API_KEY=sk-...\n"
        "  (or pass --api-key)\n"
        "\n"
        "To use a local model via Ollama instead, pass --base-url pointing at your\n"
        "Ollama server. No real key is needed there; --api-key accepts any non-empty\n"
        "value in that case.\n"
        "\n"
        "Run 'fastdocparse extract --help' for the full list of options.",
        err=True,
    )
    raise typer.Exit(code=1)


def _version_callback(value: bool) -> None:
    if not value:
        return

    typer.echo(f"fastdocparse {__version__}")
    raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    return None


@app.command()
def extract(
    file: Path = typer.Argument(..., exists=True, readable=True, help="Path to the PDF/PNG/JPG document."),
    schema: Path = typer.Argument(..., exists=True, readable=True, help="Path to a .json or .yaml schema file listing the fields to extract."),
    model: str = typer.Option("gpt-4o-mini", "--model", "-m", envvar="FASTDOCPARSE_MODEL", help="Model name, e.g. gpt-4o-mini, llama3."),
    base_url: str | None = typer.Option(None, "--base-url", envvar="FASTDOCPARSE_BASE_URL", help="OpenAI-compatible API base URL. Omit for OpenAI; use e.g. http://localhost:11434/v1 for Ollama."),
    api_key: str | None = typer.Option(None, "--api-key", envvar=["LLM_API_KEY", "OPENAI_API_KEY"], help="API key. Not needed for local Ollama."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write the JSON result to this file instead of printing it."),
    kind: str | None = typer.Option(None, "--kind", help="Override ingestion routing (e.g. 'docx' for a custom handler loaded via FASTDOCPARSE_PLUGINS). Defaults to auto-detecting pdf/image from the file extension."),
):
    """Extract the fields defined in SCHEMA from FILE and print the result as JSON."""
    if kind is None and file.suffix.lower() not in SUPPORTED_EXTENSIONS:
        typer.echo(f"Unsupported file type {file.suffix!r}. Supported: .pdf, .png, .jpg, .jpeg (or pass --kind).", err=True)
        raise typer.Exit(code=1)

    try:
        doc_schema = Schema.from_file(schema)
    except (ValidationError, ValueError, OSError) as e:
        typer.echo(f"Could not load schema from {schema}: {e}", err=True)
        raise typer.Exit(code=1)

    _check_llm_credentials(base_url, api_key)

    client = LLMClient(base_url=base_url, api_key=api_key, model=model)
    document_parser = DocumentParser(client=client)

    is_image = file.suffix.lower() in IMAGE_EXTENSIONS
    document_bytes = file.read_bytes()

    try:
        result = document_parser.extract(document_bytes, doc_schema, is_image=is_image, kind=kind)
    except EmptyDocumentError as e:
        typer.echo(f"Extraction failed: {e}", err=True)
        raise typer.Exit(code=1)
    except LLMClientError as e:
        typer.echo(f"Could not complete extraction: {e}", err=True)
        raise typer.Exit(code=1)
    except UnknownIngestionKindError as e:
        typer.echo(f"{e} (check --kind is spelled correctly and its plugin is loaded via FASTDOCPARSE_PLUGINS)", err=True)
        raise typer.Exit(code=1)
    except ValueError as e:
        typer.echo(f"Extraction failed: {e}", err=True)
        raise typer.Exit(code=1)

    result_json = json.dumps(result, indent=2, default=str)

    if output:
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(result_json)
        except OSError as e:
            typer.echo(f"Could not write output to {output}: {e}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"Wrote result to {output}")
    else:
        typer.echo(result_json)


@app.command(name="schema-from-text")
def schema_from_text(
    description: str = typer.Argument(..., help="Plain-English description of the fields you want extracted, e.g. \"invoice number, total price, and a list of line items with product name and quantity\"."),
    output: Path = typer.Option(..., "--output", "-o", help="Where to save the generated schema (.json)."),
    model: str = typer.Option("gpt-4o-mini", "--model", "-m", envvar="FASTDOCPARSE_MODEL", help="Model name, e.g. gpt-4o-mini, llama3."),
    base_url: str | None = typer.Option(None, "--base-url", envvar="FASTDOCPARSE_BASE_URL", help="OpenAI-compatible API base URL. Omit for OpenAI; use e.g. http://localhost:11434/v1 for Ollama."),
    api_key: str | None = typer.Option(None, "--api-key", envvar=["LLM_API_KEY", "OPENAI_API_KEY"], help="API key. Not needed for local Ollama."),
):
    """Turn a plain-English description of the fields you want into a schema file.

    Review the generated file before using it with `extract` — the LLM proposes field
    names and types from your description, and a wrong guess here affects every
    document you later run against this schema.
    """
    _check_llm_credentials(base_url, api_key)

    client = LLMClient(base_url=base_url, api_key=api_key, model=model)
    try:
        doc_schema = compile_schema_from_description(description, client)
    except (ValueError, LLMClientError) as e:
        typer.echo(f"Could not generate a schema: {e}", err=True)
        raise typer.Exit(code=1)

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(doc_schema.model_dump(), indent=2))
    except OSError as e:
        typer.echo(f"Could not write schema to {output}: {e}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Saved schema '{doc_schema.name}' with {len(doc_schema.fields)} field(s) to {output}")
    typer.echo("Review it, then run: fastdocparse extract <your_document> " + str(output))


if __name__ == "__main__":
    app()
