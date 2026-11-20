"""Render the parsed document model as readable, page-aware Markdown."""

from __future__ import annotations

from .models import ParsedDocument, ParsedElement, ParsedPage


def parsed_document_to_markdown(document: ParsedDocument, structured_mode: bool = False) -> str:
    """Render parsed structure for text-based consumers such as LLM extraction.

    The structured document remains the canonical representation; this is a
    convenient view that preserves page boundaries and table rows/cells. It is
    intentionally not a replacement for the source geometry or provenance.
    """
    pages: list[str] = []
    for page in document.pages:
        body = _page_to_markdown(page, structured_mode=structured_mode)
        if body:
            pages.append(f"--- PAGE {page.number} ---\n{body}")
    return "\n\n".join(pages)


def _page_to_markdown(page: ParsedPage, structured_mode: bool = False) -> str:
    elements = sorted(
        page.elements,
        key=lambda element: (
            element.bbox.y0 if element.bbox is not None else 0.0,
            element.bbox.x0 if element.bbox is not None else 0.0,
        ),
    )
    rendered = [
        text
        for element in elements
        if (text := _element_to_markdown(element, page, structured_mode=structured_mode))
    ]
    return "\n\n".join(rendered)


def _element_to_markdown(element: ParsedElement, page: ParsedPage, structured_mode: bool = False) -> str:
    if element.type == "table":
        table = _table_to_markdown(element)
        if table and element.source == "pdf_layout_inference":
            return "> Inferred table; row grouping and headers may be approximate.\n\n" + table
        return table
    if element.type in {"text_block", "line", "paragraph", "heading", "title", "caption"}:
        text = element.text.strip()
        if structured_mode and element.bbox is not None:
            x_position = int(element.bbox.x0 * page.width)
            return f"[X:{x_position}] {text}"
        return text
    # Words are children of lines and blocks, and images have no recognized text.
    # Emitting either here would duplicate text or suggest image content we do not have.
    return ""


def _table_to_markdown(table: ParsedElement) -> str:
    if not table.children:
        return ""

    if all(child.type == "table_row" for child in table.children):
        rows = [
            [cell.text.strip().replace("\n", " ") for cell in row.children]
            for row in table.children
        ]
        headers = table.attributes.get("column_headers")
        if isinstance(headers, list) and headers:
            header = [str(value).strip() for value in headers]
        else:
            header = []
    else:
        # PyMuPDF's table adapter stores cells as children with row/column indexes.
        indexed: dict[int, dict[int, str]] = {}
        for cell in table.children:
            if cell.type != "table_cell":
                continue
            row_index = cell.attributes.get("row")
            column_index = cell.attributes.get("column")
            if isinstance(row_index, int) and isinstance(column_index, int):
                indexed.setdefault(row_index, {})[column_index] = cell.text.strip().replace("\n", " ")
        if not indexed:
            return ""
        width = max((max(row) for row in indexed.values()), default=-1) + 1
        rows = [
            [indexed.get(row_index, {}).get(column_index, "") for column_index in range(width)]
            for row_index in range(max(indexed) + 1)
        ]
        if len(rows) > 1:
            # Detected PDF grids have no separate header annotation; use the first
            # extracted row as a header only when there are further data rows.
            header = rows.pop(0)
        else:
            # One-row layouts (for example seller/buyer contact panels) have no
            # evidence that their only row contains column names.
            header = []

    width = max([len(header), *(len(row) for row in rows)], default=0)
    if width == 0:
        return ""
    if not header:
        header = [f"Column {index + 1}" for index in range(width)]
    header = (header + [""] * width)[:width]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
    for row in rows:
        padded = (row + [""] * width)[:width]
        if any(padded):
            lines.append("| " + " | ".join(padded) + " |")
    return "\n".join(lines)
