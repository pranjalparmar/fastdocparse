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
from .config import ExtractionConfig
from .llm_client import LLMClient, LLMClientError
from .parser import DocumentParser, EmptyDocumentError, UnknownIngestionKindError
from .parsing import parse_pdf_document
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


def _load_schema_or_exit(path: Path) -> Schema:
    """Load a user-supplied schema, preserving the CLI's friendly error contract."""
    try:
        return Schema.from_file(path)
    except (ValidationError, ValueError, OSError) as e:
        typer.echo(f"Could not load schema from {path}: {e}", err=True)
        raise typer.Exit(code=1)


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
    schema: Path | None = typer.Argument(None, exists=True, readable=True, help="Path to a .json or .yaml schema file listing the fields to extract."),
    model: str = typer.Option("gpt-4o-mini", "--model", "-m", envvar="FASTDOCPARSE_MODEL", help="Model name, e.g. gpt-4o-mini, llama3."),
    base_url: str | None = typer.Option(None, "--base-url", envvar="FASTDOCPARSE_BASE_URL", help="OpenAI-compatible API base URL. Omit for OpenAI; use e.g. http://localhost:11434/v1 for Ollama."),
    api_key: str | None = typer.Option(None, "--api-key", envvar=["LLM_API_KEY", "OPENAI_API_KEY"], help="API key. Not needed for local Ollama."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write the JSON result to this file instead of printing it."),
    kind: str | None = typer.Option(None, "--kind", help="Override ingestion routing (e.g. 'docx' for a custom handler loaded via FASTDOCPARSE_PLUGINS). Defaults to auto-detecting pdf/image from the file extension."),
    max_pages: int = typer.Option(15, "--max-pages", help="Maximum pages to process before truncating (default: 15)."),
    chunk_max_tokens: int = typer.Option(3000, "--chunk-max-tokens", help="Maximum token estimate per LLM chunk (default: 3000)."),
    pdf_render_dpi: int = typer.Option(150, "--pdf-render-dpi", help="DPI used to render PDF pages for OCR (default: 150)."),
    max_image_dim: int = typer.Option(1536, "--max-image-dim", help="Maximum image dimension before OCR downscaling (default: 1536)."),
    ocr_min_confidence: float = typer.Option(0.3, "--ocr-min-confidence", help="Discard OCR text below this confidence, from 0 to 1 (default: 0.3)."),
    max_concurrent_chunks: int = typer.Option(1, "--max-concurrent-chunks", help="Maximum LLM chunks processed concurrently (default: 1)."),
):
    """Extract the fields defined in SCHEMA from FILE and print the result as JSON."""
    if schema is None:
        typer.echo(
            "Error: a schema file is required.\n"
            "\n"
            "Quickest way to create one — describe your fields in plain English:\n"
            "  fastdocparse schema-from-text \"invoice number, total amount, and line items\" --output my_schema.json\n"
            "\n"
            "Or start from a bundled example schema:\n"
            "  fastdocparse list-schemas\n"
            "\n"
            "Then extract with:\n"
            "  fastdocparse extract " + str(file) + " my_schema.json",
            err=True,
        )
        raise typer.Exit(code=1)

    if kind is None and file.suffix.lower() not in SUPPORTED_EXTENSIONS:
        typer.echo(f"Unsupported file type {file.suffix!r}. Supported: .pdf, .png, .jpg, .jpeg (or pass --kind).", err=True)
        raise typer.Exit(code=1)

    doc_schema = _load_schema_or_exit(schema)

    _check_llm_credentials(base_url, api_key)

    try:
        config = ExtractionConfig(
            max_pages=max_pages,
            chunk_max_tokens=chunk_max_tokens,
            pdf_render_dpi=pdf_render_dpi,
            max_image_dim=max_image_dim,
            ocr_min_confidence=ocr_min_confidence,
            max_concurrent_chunks=max_concurrent_chunks,
        )
    except ValueError as e:
        typer.echo(f"Extraction failed: {e}", err=True)
        raise typer.Exit(code=1)

    client = LLMClient(base_url=base_url, api_key=api_key, model=model)
    document_parser = DocumentParser(client=client, config=config)

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


def _table_rows(table):
    """Return table rows uniformly for inferred and detector-produced tables."""
    if table.children and all(child.type == "table_row" for child in table.children):
        return [row.children for row in table.children]

    rows = {}
    for cell in table.children:
        row_index = cell.attributes.get("row", 0)
        rows.setdefault(row_index, []).append(cell)
    return [
        sorted(cells, key=lambda cell: cell.attributes.get("column", 0))
        for _, cells in sorted(rows.items())
    ]


def _markdown_escape(text: str) -> str:
    """Escape text for a Markdown table cell while preserving line breaks."""
    return text.strip().replace("|", "\\|").replace("\r\n", "\n").replace("\n", "<br>")


def _parsed_document_to_markdown(parsed, file: Path) -> str:
    """Render the positioned parse as readable Markdown with coordinate comments."""
    lines = [f"# Parsed document: {file.name}", "", "<!-- Generated by fastdocparse inspect; coordinates are PDF points. -->", ""]
    for page in parsed.pages:
        lines.extend(
            [
                f"## Page {page.number}",
                "",
                f"Page size: {page.width:.1f} × {page.height:.1f} {page.units}",
                "",
            ]
        )
        tables = [element for element in page.elements if element.type == "table"]
        images = [element for element in page.elements if element.type == "image"]
        table_blocks: set[int] = set()
        for table in tables:
            for row in _table_rows(table):
                table_blocks.update(
                    id(element)
                    for element in page.elements
                    if element.type == "text_block"
                    and any(
                        element.bbox is not None
                        and cell.bbox is not None
                        and element.bbox.x0 <= cell.bbox.x0 <= element.bbox.x1
                        and element.bbox.y0 <= cell.bbox.y0 <= element.bbox.y1
                        for cell in row
                    )
                )
        for table_index, table in enumerate(tables, start=1):
            label = "inferred" if table.attributes.get("inferred") else "detected"
            lines.extend([f"### Table {table_index} ({label})", ""])
            if table.bbox is not None:
                bbox = tuple(round(value, 1) for value in table.bbox.to_source(page.width, page.height))
                lines.extend([f"<!-- table_bbox_pt: {bbox}; source: {table.source} -->", ""])

            rows = _table_rows(table)
            column_count = max((len(row) for row in rows), default=0)
            if not column_count:
                lines.extend(["No table cells were returned.", ""])
                continue

            headers = table.attributes.get("column_headers") or [
                f"Column {column + 1}" for column in range(column_count)
            ]
            if len(headers) < column_count:
                headers = list(headers) + [
                    f"Column {column + 1}" for column in range(len(headers), column_count)
                ]
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("| " + " | ".join("---" for _ in headers) + " |")
            row_metadata: list[str] = []
            for row_index, cells in enumerate(rows, start=1):
                values = [_markdown_escape(cell.text) for cell in cells]
                values.extend("" for _ in range(column_count - len(values)))
                lines.append("| " + " | ".join(values) + " |")
                cell_boxes = [
                    tuple(round(value, 1) for value in cell.bbox.to_source(page.width, page.height))
                    if cell.bbox
                    else None
                    for cell in cells
                ]
                row_bbox = (
                    table.children[row_index - 1].bbox
                    if row_index <= len(table.children) and table.children[row_index - 1].type == "table_row"
                    else None
                )
                row_box = (
                    tuple(round(value, 1) for value in row_bbox.to_source(page.width, page.height))
                    if row_bbox
                    else None
                )
                row_metadata.append(
                    f"<!-- row {row_index} bbox_pt: {row_box}; cell_bboxes_pt: {cell_boxes} -->"
                )
            lines.extend(row_metadata)
            lines.append("")

        related_caption_indexes = {
            relation.get("target_index")
            for relation in page.relations
            if relation.get("type") == "caption_of"
        }
        if images:
            lines.extend(["### Figures and images", ""])
            for image_index, image in enumerate(images, start=1):
                bbox = (
                    tuple(round(value, 1) for value in image.bbox.to_source(page.width, page.height))
                    if image.bbox
                    else None
                )
                role = image.attributes.get("role", "embedded_image")
                dimensions = ""
                if image.attributes.get("width_px") and image.attributes.get("height_px"):
                    dimensions = f" ({image.attributes['width_px']} × {image.attributes['height_px']} px)"
                lines.append(f"- Image {image_index}: {role}{dimensions}; bbox_pt: {bbox}")
                element_index = page.elements.index(image)
                related_caption = next(
                    (
                        relation.get("target_index")
                        for relation in page.relations
                        if relation.get("type") == "caption_of"
                        and relation.get("source_index") == element_index
                    ),
                    None,
                )
                if isinstance(related_caption, int) and related_caption < len(page.elements):
                    lines.append(f"  - Caption: {page.elements[related_caption].text.strip()}")
            lines.append("")

        other_blocks = [
            (index, element)
            for index, element in enumerate(page.elements)
            if element.type == "text_block"
            and id(element) not in table_blocks
            and index not in related_caption_indexes
        ]
        native_blocks = [(index, block) for index, block in other_blocks if block.source != "positioned_ocr"]
        ocr_blocks = [(index, block) for index, block in other_blocks if block.source == "positioned_ocr"]
        if native_blocks:
            lines.extend(["### Other text", ""])
            for _, block in native_blocks:
                if block.bbox is not None:
                    bbox = tuple(round(value, 1) for value in block.bbox.to_source(page.width, page.height))
                    lines.append(f"<!-- bbox_pt: {bbox} -->")
                lines.extend([block.text.strip(), ""])

        if ocr_blocks:
            lines.extend(
                [
                    "### Positioned OCR lines (reading order is approximate)",
                    "",
                    "| Y position (pt) | X range (pt) | Text | Confidence |",
                    "| ---: | ---: | --- | ---: |",
                ]
            )
            for _, block in ocr_blocks:
                if block.bbox is None:
                    position = ("", "")
                else:
                    x0, y0, x1, _ = block.bbox.to_source(page.width, page.height)
                    position = (f"{y0:.1f}", f"{x0:.1f}–{x1:.1f}")
                confidence = f"{block.confidence:.2f}" if block.confidence is not None else ""
                lines.append(
                    "| "
                    + " | ".join(
                        (
                            position[0],
                            position[1],
                            _markdown_escape(block.text),
                            confidence,
                        )
                    )
                    + " |"
                )
            lines.append("")
        if not tables and not native_blocks and not ocr_blocks:
            lines.extend(["No native text found (this may be a scanned PDF).", ""])

    return "\n".join(lines).rstrip() + "\n"


@app.command()
def inspect(
    file: Path = typer.Argument(..., exists=True, readable=True, help="Path to the PDF to parse."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Save the parsed document as JSON or Markdown (.json/.md)."),
    output_format: str = typer.Option("auto", "--format", help="Output format: auto (from filename), json, or markdown."),
    max_pages: int = typer.Option(15, "--max-pages", help="Maximum number of pages to parse (default: 15)."),
):
    """Parse a PDF without an LLM and display detected text, tables, and coordinates."""
    if file.suffix.lower() != ".pdf":
        typer.echo("Only PDF files are supported by inspect.", err=True)
        raise typer.Exit(code=1)

    try:
        parsed = parse_pdf_document(file.read_bytes(), max_pages=max_pages)
    except (OSError, ValueError) as exc:
        typer.echo(f"Could not parse {file}: {exc}", err=True)
        raise typer.Exit(code=1)

    if not parsed.pages:
        typer.echo(f"No PDF pages could be parsed from {file}.", err=True)
        raise typer.Exit(code=1)

    if output_format not in {"auto", "json", "markdown"}:
        typer.echo("--format must be one of: auto, json, markdown.", err=True)
        raise typer.Exit(code=1)
    if output_format == "auto":
        if output is None:
            output_format = "console"
        else:
            output_format = "markdown" if output.suffix.lower() in {".md", ".markdown"} else "json"

    if output_format == "markdown":
        markdown = _parsed_document_to_markdown(parsed, file)
        if output is None:
            typer.echo(markdown, nl=False)
            return
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(markdown, encoding="utf-8")
        except OSError as exc:
            typer.echo(f"Could not write parsed Markdown to {output}: {exc}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"Wrote Markdown parse to {output}")
        return

    if output_format == "json" and output is None:
        typer.echo("--format json requires --output <file.json>.", err=True)
        raise typer.Exit(code=1)

    if output is not None:
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(parsed.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError as exc:
            typer.echo(f"Could not write parsed JSON to {output}: {exc}", err=True)
            raise typer.Exit(code=1)

    typer.echo(f"PDF: {file}")
    typer.echo(f"Pages: {parsed.metadata.get('pages_processed', len(parsed.pages))} processed / {parsed.metadata.get('page_count', len(parsed.pages))} total")
    for page in parsed.pages:
        tables = [element for element in page.elements if element.type == "table"]
        text_blocks = [element for element in page.elements if element.type == "text_block"]
        typer.echo(f"\nPage {page.number} ({page.width:.1f} × {page.height:.1f} {page.units}): {len(text_blocks)} text blocks, {len(tables)} tables")
        for table_index, table in enumerate(tables, start=1):
            label = "inferred" if table.attributes.get("inferred") else "detected"
            typer.echo(f"  Table {table_index} ({label}, source={table.source}):")
            for row_index, cells in enumerate(_table_rows(table), start=1):
                typer.echo(f"    Row {row_index}:")
                for cell in cells:
                    bbox = cell.bbox.to_source(page.width, page.height) if cell.bbox else None
                    bbox_text = f" bbox_pt={tuple(round(value, 1) for value in bbox)}" if bbox else ""
                    typer.echo(f"      - {cell.text.strip()!r}{bbox_text}")

        if text_blocks:
            typer.echo("  Other positioned text blocks:")
            for block in text_blocks:
                bbox = block.bbox.to_source(page.width, page.height) if block.bbox else None
                bbox_text = f" bbox_pt={tuple(round(value, 1) for value in bbox)}" if bbox else ""
                typer.echo(f"    - {block.text.strip()!r}{bbox_text}")
        elif not tables:
            typer.echo("  No native text found (likely a scanned PDF; positioned OCR is not connected yet).")

    if output is not None:
        typer.echo(f"\nFull JSON saved to: {output}")


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


def _bundled_schema_files(schemas_dir: Path) -> list[Path]:
    return sorted(
        path
        for pattern in ("*.json", "*.yaml", "*.yml")
        for path in schemas_dir.glob(pattern)
    )


@app.command(name="list-schemas")
def list_schemas():
    """List the bundled example schemas you can copy as a starting point.

    Copy any of the listed files into your project and pass the copy to `extract`.
    This is the fastest way to get started without an LLM-generated schema.
    """
    schemas_dir = Path(__file__).parent / "schemas"
    schema_files = _bundled_schema_files(schemas_dir)
    if not schema_files:
        typer.echo("No bundled schemas found.", err=True)
        raise typer.Exit(code=1)

    typer.echo("Bundled example schemas (copy one as your starting point):\n")

    # Load each schema rather than listing its path. A bare path tells the reader
    # nothing about which one fits their document, so the only way to choose was
    # to open both files -- which is the thing this command exists to save.
    described: list[tuple[str, str]] = []
    for path in schema_files:
        try:
            schema = Schema.from_file(path)
        except (ValidationError, ValueError, OSError):
            # A bundled schema that will not parse is a packaging fault, not a
            # reason to show nothing: the filename is still copyable, which is
            # what this command is for.
            described.append((path.name, ""))
            continue
        preview = ", ".join(f.name for f in schema.fields[:3])
        if len(schema.fields) > 3:
            preview += ", ..."
        plural = "s" if len(schema.fields) != 1 else ""
        described.append(
            (path.name, f"{schema.name}: {preview} ({len(schema.fields)} field{plural})")
        )

    width = max(len(name) for name, _ in described)
    for name, summary in described:
        typer.echo(f"  {name.ljust(width)}  {summary}".rstrip())

    # The directory is printed once instead of on every row: it is the same for
    # all of them, and repeating it is what pushed the useful part off the line.
    typer.echo(
        f"\nThey are in:\n  {schemas_dir}\n"
        "\nTo use one directly:\n"
        "  fastdocparse extract document.pdf " + str(schema_files[0])
    )


@app.command(name="validate-schema")
def validate_schema(
    schema: Path = typer.Argument(..., exists=True, readable=True, help="Path to the .json or .yaml schema file to validate."),
):
    """Check that a schema file is well-formed without needing a document or LLM credentials."""
    doc_schema = _load_schema_or_exit(schema)

    fields_count = len(doc_schema.fields)
    examples_count = len(doc_schema.examples) if doc_schema.examples else 0
    fields_plural = "s" if fields_count != 1 else ""
    examples_plural = "s" if examples_count != 1 else ""
    if examples_count > 0:
        typer.echo(
            f"Schema '{doc_schema.name}' is valid: {fields_count} field{fields_plural}, "
            f"{examples_count} example{examples_plural}."
        )
    else:
        typer.echo(f"Schema '{doc_schema.name}' is valid: {fields_count} field{fields_plural}.")


if __name__ == "__main__":
    app()
