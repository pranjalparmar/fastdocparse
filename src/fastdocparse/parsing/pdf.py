"""PyMuPDF adapter that preserves native PDF text geometry."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any

import pymupdf

from ..pdf_utils import _bbox_overlaps, _safe_open_pdf
from .models import BBox, ParsedDocument, ParsedElement, ParsedPage

logger = logging.getLogger(__name__)
_ROW_TOP_TOLERANCE = 0.004  # about 3.4 pt on an A4 page
_COLUMN_TOLERANCE = 0.025  # about 15 pt on an A4 page
_MAX_TABLE_ROW_GAP = 0.08  # reject unrelated lower-page blocks / footers
_HEADER_SEARCH_DISTANCE = 0.12
_CURRENCY_CODES = {"EUR", "USD", "GBP", "CHF", "CAD", "AUD", "JPY", "CNY"}


def _normalized_bbox(
    raw_bbox: tuple[float, float, float, float] | list[float],
    page: Any,
) -> BBox | None:
    """Convert PyMuPDF's unrotated point coordinates to normalized page coordinates."""
    try:
        rect = pymupdf.Rect(raw_bbox)
        if page.rotation:
            rect = rect * page.rotation_matrix
        page_rect = page.rect
        rect &= page_rect
        if rect.is_empty or rect.is_infinite:
            return None
        width, height = page_rect.width, page_rect.height
        if width <= 0 or height <= 0:
            return None
        return BBox(
            x0=max(0.0, min(1.0, (rect.x0 - page_rect.x0) / width)),
            y0=max(0.0, min(1.0, (rect.y0 - page_rect.y0) / height)),
            x1=max(0.0, min(1.0, (rect.x1 - page_rect.x0) / width)),
            y1=max(0.0, min(1.0, (rect.y1 - page_rect.y0) / height)),
        )
    except (TypeError, ValueError, RuntimeError):
        logger.debug("Skipping invalid PDF bounding box %r", raw_bbox, exc_info=True)
        return None


def _page_elements(
    page: Any,
    ocr_min_confidence: float = 0.3,
    ocr_scans: bool = True,
) -> list[ParsedElement]:
    """Build positioned text, image, and table elements for one PDF page."""
    words_by_block_line: dict[int, dict[int, list[tuple[Any, ...]]]] = defaultdict(lambda: defaultdict(list))
    try:
        for word in page.get_text("words", sort=True):
            if len(word) >= 8 and str(word[4]).strip():
                words_by_block_line[int(word[5])][int(word[6])].append(word)
    except (RuntimeError, TypeError, ValueError):
        logger.debug("Could not extract PDF words; falling back to text blocks.", exc_info=True)

    try:
        text_dict = page.get_text("dict", sort=True)
    except (RuntimeError, TypeError, ValueError):
        logger.debug("Could not extract structured PDF text.", exc_info=True)
        return []

    elements: list[ParsedElement] = []
    table_bboxes: list[tuple[float, float, float, float]] = []
    try:
        table_finder = page.find_tables()
        for table_index, table in enumerate(table_finder.tables):
            try:
                grid = table.extract()
                rows = table.rows
                table_bbox = tuple(float(value) for value in table.bbox)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                logger.debug("Skipping failed PDF table extraction.", exc_info=True)
                continue

            cells: list[ParsedElement] = []
            for row_index, row in enumerate(rows):
                for column_index, cell_bbox in enumerate(row.cells):
                    if cell_bbox is None:
                        continue
                    try:
                        cell_text = grid[row_index][column_index]
                    except (IndexError, TypeError):
                        cell_text = ""
                    cell_text = str(cell_text or "").strip()
                    cells.append(
                        ParsedElement(
                            type="table_cell",
                            text=cell_text,
                            bbox=_normalized_bbox(cell_bbox, page),
                            source="pdf_table",
                            attributes={"row": row_index, "column": column_index},
                        )
                    )

            normalized_table_bbox = _normalized_bbox(table_bbox, page)
            if normalized_table_bbox is not None:
                table_bboxes.append(table_bbox)
            elements.append(
                ParsedElement(
                    type="table",
                    bbox=normalized_table_bbox,
                    source="pdf_table",
                    children=cells,
                    attributes={"table_index": table_index, "row_count": len(rows)},
                )
            )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        # Table recognition is an optional PyMuPDF capability; ordinary text parsing
        # should still succeed when it's unavailable or a document confuses it.
        logger.debug("Could not discover PDF tables.", exc_info=True)

    for block_index, block in enumerate(text_dict.get("blocks", [])):
        if block.get("type") == 1:
            image_bbox = _normalized_bbox(block.get("bbox", ()), page)
            if image_bbox is None:
                continue
            is_page_image = image_bbox.x0 <= 0.01 and image_bbox.y0 <= 0.01 and image_bbox.x1 >= 0.99 and image_bbox.y1 >= 0.99
            elements.append(
                ParsedElement(
                    type="image",
                    bbox=image_bbox,
                    source="pdf_image",
                    attributes={
                        "image_index": block.get("number", block_index),
                        "width_px": block.get("width"),
                        "height_px": block.get("height"),
                        "format": block.get("ext"),
                        "role": "full_page_scan" if is_page_image else "embedded_image",
                    },
                )
            )
            continue
        if block.get("type") != 0:
            continue

        block_bbox = block.get("bbox", ())
        if any(_bbox_overlaps(block_bbox, table_bbox) for table_bbox in table_bboxes):
            # Table text is represented by its cells below, avoiding duplicate content.
            continue

        lines: list[ParsedElement] = []
        block_text_lines: list[str] = []
        for line_index, line in enumerate(block.get("lines", [])):
            line_words = words_by_block_line.get(block_index, {}).get(line_index, [])
            word_elements: list[ParsedElement] = []
            for word in line_words:
                word_text = str(word[4]).strip()
                word_bbox = _normalized_bbox(word[:4], page)
                if word_text:
                    word_elements.append(
                        ParsedElement(type="word", text=word_text, bbox=word_bbox, source="pdf_text")
                    )

            spans = line.get("spans", [])
            line_text = "".join(str(span.get("text", "")) for span in spans).strip()
            if not line_text and word_elements:
                line_text = " ".join(word.text for word in word_elements)
            if not line_text:
                continue
            block_text_lines.append(line_text)
            lines.append(
                ParsedElement(
                    type="line",
                    text=line_text,
                    bbox=_normalized_bbox(line.get("bbox", ()), page),
                    source="pdf_text",
                    children=word_elements,
                )
            )

        block_text = "\n".join(block_text_lines).strip()
        if not block_text:
            continue
        elements.append(
            ParsedElement(
                type="text_block",
                text=block_text,
                bbox=_normalized_bbox(block_bbox, page),
                source="pdf_text",
                children=lines,
            )
        )

    elements = _infer_aligned_tables(elements)
    has_full_page_scan = any(
        element.type == "image" and element.attributes.get("role") == "full_page_scan"
        for element in elements
    )
    has_native_text = any(
        element.type == "text_block" and element.source == "pdf_text"
        for element in elements
    )
    if ocr_scans and has_full_page_scan and not has_native_text:
        elements.extend(_ocr_page_elements(page, min_confidence=ocr_min_confidence))
    elements.sort(
        key=lambda element: (
            element.bbox.y0 if element.bbox else 0.0,
            element.bbox.x0 if element.bbox else 0.0,
        )
    )
    return elements


def _ocr_page_elements(page: Any, min_confidence: float = 0.3) -> list[ParsedElement]:
    """OCR an image-only page and map detection boxes back to normalized page space."""
    from ..ocr_engine import extract_positioned_text_from_image_ocr

    try:
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
        detections = extract_positioned_text_from_image_ocr(
            pixmap.tobytes("png"), min_confidence=min_confidence
        )
    except (RuntimeError, TypeError, ValueError):
        logger.debug("Could not render image-only PDF page for OCR.", exc_info=True)
        return []

    if pixmap.width <= 0 or pixmap.height <= 0:
        return []
    positioned_words: list[ParsedElement] = []
    for text, (x0, y0, x1, y1), confidence in detections:
        bbox = BBox(
            x0=max(0.0, min(1.0, x0 / pixmap.width)),
            y0=max(0.0, min(1.0, y0 / pixmap.height)),
            x1=max(0.0, min(1.0, x1 / pixmap.width)),
            y1=max(0.0, min(1.0, y1 / pixmap.height)),
        )
        word = ParsedElement(
            type="word",
            text=text,
            bbox=bbox,
            source="positioned_ocr",
            confidence=confidence,
        )
        positioned_words.append(word)

    # Group OCR detections into reading lines using vertical overlap, retaining
    # individual word boxes as children. Only join detections when their vertical
    # overlap is substantial; center-distance alone merges adjacent lines on skewed
    # or low-resolution scans.
    line_groups: list[list[ParsedElement]] = []
    for word in sorted(
        positioned_words,
        key=lambda item: ((item.bbox.y0 + item.bbox.y1) / 2, item.bbox.x0),
    ):
        assert word.bbox is not None
        word_height = max(0.001, word.bbox.y1 - word.bbox.y0)
        matching_group: list[ParsedElement] | None = None
        best_overlap = 0.0
        for group in line_groups:
            group_bbox = _union_bboxes([item.bbox for item in group])
            if group_bbox is None:
                continue
            overlap = max(0.0, min(word.bbox.y1, group_bbox.y1) - max(word.bbox.y0, group_bbox.y0))
            group_height = max(0.001, group_bbox.y1 - group_bbox.y0)
            overlap_ratio = overlap / min(word_height, group_height)
            center_delta = abs(
                (word.bbox.y0 + word.bbox.y1 - group_bbox.y0 - group_bbox.y1) / 2
            )
            if overlap_ratio >= 0.55 and center_delta <= max(word_height, group_height) * 0.3:
                if overlap_ratio > best_overlap:
                    matching_group = group
                    best_overlap = overlap_ratio
        if matching_group is None:
            line_groups.append([word])
        else:
            matching_group.append(word)

    elements: list[ParsedElement] = []
    for group in line_groups:
        group.sort(key=lambda item: item.bbox.x0 if item.bbox else 0.0)
        bbox = _union_bboxes([item.bbox for item in group])
        confidence_values = [item.confidence for item in group if item.confidence is not None]
        confidence = sum(confidence_values) / len(confidence_values) if confidence_values else None
        line_text = " ".join(item.text for item in group)
        line = ParsedElement(
            type="line",
            text=line_text,
            bbox=bbox,
            source="positioned_ocr",
            confidence=confidence,
            children=group,
        )
        elements.append(
            ParsedElement(
                type="text_block",
                text=line_text,
                bbox=bbox,
                source="positioned_ocr",
                confidence=confidence,
                children=[line],
            )
        )
    return elements


def _leaf_words(element: ParsedElement) -> list[ParsedElement]:
    """Return positioned word descendants, or the element if it is a word."""
    if element.type == "word":
        return [element]
    words: list[ParsedElement] = []
    for child in element.children:
        words.extend(_leaf_words(child))
    return words


def _image_caption_relations(elements: list[ParsedElement]) -> list[dict[str, Any]]:
    """Link images to nearby, explicitly labeled caption text blocks.

    Caption recognition is intentionally conservative. Unlabeled nearby prose is
    not promoted to a caption, and all source text remains in the page elements.
    Relation endpoints are zero-based indexes into the sorted page element list.
    """
    caption_pattern = re.compile(
        r"^\s*(?:figure|fig\.?|photo|photograph|image|diagram|chart|illustration)\s*"
        r"(?:\d+|[A-Z]|[IVX]+)?\s*[:.\-–]",
        flags=re.IGNORECASE,
    )
    text_blocks = [
        (index, element)
        for index, element in enumerate(elements)
        if element.type == "text_block" and element.bbox is not None and caption_pattern.search(element.text)
    ]
    image_blocks = [
        (index, element)
        for index, element in enumerate(elements)
        if element.type == "image" and element.bbox is not None and element.attributes.get("role") != "full_page_scan"
    ]

    relations: list[dict[str, Any]] = []
    used_captions: set[int] = set()
    for image_index, image in image_blocks:
        assert image.bbox is not None
        candidates: list[tuple[float, int, ParsedElement]] = []
        for caption_index, caption in text_blocks:
            if caption_index in used_captions or caption.bbox is None:
                continue
            horizontal_overlap = max(
                0.0,
                min(image.bbox.x1, caption.bbox.x1) - max(image.bbox.x0, caption.bbox.x0),
            )
            min_width = max(0.001, min(image.bbox.x1 - image.bbox.x0, caption.bbox.x1 - caption.bbox.x0))
            horizontal_overlap_ratio = horizontal_overlap / min_width
            center_distance = abs(
                (image.bbox.x0 + image.bbox.x1 - caption.bbox.x0 - caption.bbox.x1) / 2
            )
            if horizontal_overlap_ratio < 0.2 and center_distance > 0.12:
                continue

            if caption.bbox.y0 >= image.bbox.y1:
                vertical_gap = caption.bbox.y0 - image.bbox.y1
            elif caption.bbox.y1 <= image.bbox.y0:
                vertical_gap = image.bbox.y0 - caption.bbox.y1
            else:
                vertical_gap = 0.0
            if vertical_gap > 0.06:
                continue
            candidates.append((vertical_gap + center_distance * 0.1, caption_index, caption))

        if candidates:
            _, caption_index, caption = min(candidates, key=lambda item: item[0])
            used_captions.add(caption_index)
            relations.append(
                {
                    "type": "caption_of",
                    "source_index": image_index,
                    "target_index": caption_index,
                    "confidence": 0.9,
                }
            )
    return relations


def _infer_aligned_tables(elements: list[ParsedElement]) -> list[ParsedElement]:
    """Conservatively group repeated, horizontally aligned text blocks into table rows.

    This handles simple unruled tables in digital PDFs where PyMuPDF's table detector
    finds no drawn grid. It deliberately requires at least two aligned multi-column
    rows, and only groups block starts that align closely on the page. It is a layout
    heuristic, not a replacement for learned table-structure recognition.
    """
    candidates = [
        element
        for element in elements
        if element.type == "text_block" and element.text.strip() and element.bbox is not None
    ]
    candidates.sort(key=lambda element: (element.bbox.y0, element.bbox.x0))

    # Candidate rows are formed from blocks whose top edges share a baseline band.
    baseline_rows: list[list[ParsedElement]] = []
    for element in candidates:
        assert element.bbox is not None
        matching_rows = []
        for row in baseline_rows:
            row_y = min(block.bbox.y0 for block in row if block.bbox is not None)
            if abs(element.bbox.y0 - row_y) <= _ROW_TOP_TOLERANCE and all(
                block.bbox is not None
                and abs(block.bbox.x0 - element.bbox.x0) > _COLUMN_TOLERANCE
                for block in row
            ):
                matching_rows.append(row)
        if matching_rows:
            # Choose the closest baseline band if neighboring lines fall within the
            # tolerance; do not stop at the most recent row because pages are sorted
            # primarily by y and a later candidate may belong to an earlier band.
            row = min(
                matching_rows,
                key=lambda candidate: abs(
                    element.bbox.y0
                    - min(block.bbox.y0 for block in candidate if block.bbox is not None)
                ),
            )
            row.append(element)
        else:
            baseline_rows.append([element])

    multi_column_rows = [
        sorted(row, key=lambda block: block.bbox.x0 if block.bbox is not None else 0.0)
        for row in baseline_rows
        if len(row) >= 2 and _looks_like_data_row(row)
    ]
    if len(multi_column_rows) < 2:
        return elements

    # Find repeated x-column starts across rows. Matching more than two columns is
    # allowed, but every accepted row must have at least two stable column positions.
    repeated_rows: list[list[ParsedElement]] = []
    for row in multi_column_rows:
        row_x = [block.bbox.x0 for block in row if block.bbox is not None]
        matches = 0
        for other in multi_column_rows:
            if other is row:
                continue
            other_x = [block.bbox.x0 for block in other if block.bbox is not None]
            if sum(any(abs(x - ox) <= _COLUMN_TOLERANCE for ox in other_x) for x in row_x) >= 2:
                matches += 1
                break
        if matches:
            repeated_rows.append(row)

    if len(repeated_rows) < 2:
        return elements

    # A repeated alignment elsewhere on the page is not enough: rows in one table
    # should be vertically contiguous. Keep the largest such group so a footer or
    # signature area cannot be annexed to an otherwise valid item table.
    repeated_rows.sort(key=lambda row: min(block.bbox.y0 for block in row if block.bbox is not None))
    row_groups: list[list[list[ParsedElement]]] = []
    for row in repeated_rows:
        row_y = min(block.bbox.y0 for block in row if block.bbox is not None)
        if not row_groups:
            row_groups.append([row])
            continue
        previous_y = min(
            block.bbox.y0
            for block in row_groups[-1][-1]
            if block.bbox is not None
        )
        if row_y - previous_y <= _MAX_TABLE_ROW_GAP:
            row_groups[-1].append(row)
        else:
            row_groups.append([row])
    repeated_rows = max(row_groups, key=len)
    if len(repeated_rows) < 2:
        return elements
    if not _columns_have_consistent_value_kinds(repeated_rows):
        # Repeatedly aligned key/value blocks (for example EORI, then Incoterms)
        # can look grid-like without being rows of one table. Do not promote them
        # when the same columns alternate between labels and values.
        return elements

    repeated_blocks = {id(block) for row in repeated_rows for block in row}
    table_rows: list[ParsedElement] = []
    for row_index, row in enumerate(repeated_rows):
        row_cells: list[ParsedElement] = []
        for block in row:
            assert block.bbox is not None
            # Text extraction may put several visual columns in one block when
            # their lines wrap or use independent baselines. Split by line x-start,
            # but keep wrapped lines at the same x position together as one cell.
            line_groups: list[list[ParsedElement]] = []
            for line in sorted(
                (line for line in block.children if line.text.strip() and line.bbox is not None),
                key=lambda line: (line.bbox.x0, line.bbox.y0),
            ):
                matching = next(
                    (
                        group
                        for group in line_groups
                        if abs(group[0].bbox.x0 - line.bbox.x0) <= _COLUMN_TOLERANCE
                    ),
                    None,
                )
                if matching is None:
                    line_groups.append([line])
                else:
                    matching.append(line)

            if line_groups:
                for group in line_groups:
                    group.sort(key=lambda line: line.bbox.y0)
                    cell_bbox = _union_bboxes([line.bbox for line in group])
                    row_cells.append(
                        ParsedElement(
                            type="table_cell",
                            text="\n".join(line.text for line in group),
                            bbox=cell_bbox,
                            source=block.source,
                        )
                    )
            else:
                row_cells.append(
                    ParsedElement(
                        type="table_cell",
                        text=block.text,
                        bbox=block.bbox,
                        source=block.source,
                    )
                )

        row_cells.sort(key=lambda cell: (cell.bbox.x0 if cell.bbox else 0.0, cell.bbox.y0 if cell.bbox else 0.0))
        for column_index, cell in enumerate(row_cells):
            cell.attributes.update({"row": row_index, "column": column_index})
        row_bbox = _union_bboxes([cell.bbox for cell in row_cells])
        row_text = " | ".join(cell.text.replace("\n", " ") for cell in row_cells)
        table_rows.append(
            ParsedElement(
                type="table_row",
                text=row_text,
                bbox=row_bbox,
                source="pdf_layout_inference",
                children=row_cells,
                attributes={"row": row_index},
            )
        )

    table_bbox = _union_bboxes([row.bbox for row in table_rows])
    column_headers = _infer_column_headers(table_rows, elements, repeated_blocks, table_bbox)
    inferred_table = ParsedElement(
        type="table",
        bbox=table_bbox,
        source="pdf_layout_inference",
        children=table_rows,
        attributes={
            "inferred": True,
            "row_count": len(table_rows),
            "column_headers": column_headers,
        },
    )
    return [element for element in elements if id(element) not in repeated_blocks] + [inferred_table]


def _columns_have_consistent_value_kinds(rows: list[list[ParsedElement]]) -> bool:
    """Reject aligned groups whose columns do not repeat a stable value pattern."""
    width = max((len(row) for row in rows), default=0)
    if width < 2:
        return False

    def kind(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            return "empty"
        if re.fullmatch(r"[A-Z]{3}", normalized, flags=re.IGNORECASE):
            return "currency"
        if re.fullmatch(r"[A-Z]{1,3}[\s-]*\d[\w./-]*", normalized, flags=re.IGNORECASE):
            return "identifier"
        if re.fullmatch(r"\d{6,}", re.sub(r"\s+", "", normalized)):
            return "identifier"
        if re.search(r"\b(?:kg|g|lb|lbs|cm|mm|m)\b", normalized, flags=re.IGNORECASE):
            return "measurement"
        if _looks_like_amount(normalized):
            return "amount"
        if re.fullmatch(r"[\d.,+\- ]+", normalized):
            return "number"
        return "text"

    for column_index in range(width):
        kinds = {
            kind(row[column_index].text)
            for row in rows
            if column_index < len(row) and row[column_index].text.strip()
        }
        # Sparse columns cannot establish a repeated pattern. In normal data rows,
        # at least two values per column should be available.
        if len(kinds) != 1:
            return False
    return True


def _looks_like_data_row(row: list[ParsedElement]) -> bool:
    """Reject tiny alignment noise using only text extent and page geometry.

    Do not require invoice-like descriptions, currency, dates, or numeric values:
    repeated spatial structure is applicable to tables in other document classes.
    """
    if len(row) < 2:
        return False

    valid_cells = [
        block
        for block in row
        if block.bbox is not None and block.text.strip()
    ]
    if len(valid_cells) < 2:
        return False

    # Decorative stamps and signature marks often become tiny character-sized
    # blocks. Real table cells need at least one substantial text region. This is a
    # geometric noise filter, not a content-category classifier.
    substantial_cells = [
        block
        for block in valid_cells
        if
        len(block.text.strip()) >= 3
        and block.bbox.x1 - block.bbox.x0 >= 0.035
        and block.bbox.y1 - block.bbox.y0 >= 0.006
    ]
    return len(substantial_cells) >= 2


def _infer_column_headers(
    rows: list[ParsedElement],
    elements: list[ParsedElement],
    row_block_ids: set[int],
    table_bbox: BBox | None,
) -> list[str]:
    """Use nearby printed headers, then recognizable cell values, for column labels.

    Labels are intentionally conservative: if neither a nearby heading nor a clear
    value pattern identifies a column, it remains ``Column N`` rather than guessing.
    """
    column_count = max((len(row.children) for row in rows), default=0)
    if not column_count:
        return []

    labels: list[str | None] = [None] * column_count
    column_centres: list[float] = []
    for column_index in range(column_count):
        centres = [
            (row.children[column_index].bbox.x0 + row.children[column_index].bbox.x1) / 2
            for row in rows
            if column_index < len(row.children) and row.children[column_index].bbox is not None
        ]
        column_centres.append(sum(centres) / len(centres) if centres else 0.0)

    table_top = table_bbox.y0 if table_bbox is not None else 1.0
    candidate_header_lines: list[ParsedElement] = []
    for element in elements:
        if id(element) in row_block_ids or element.type != "text_block":
            continue
        candidate_header_lines.extend(element.children or [element])

    # PDF extraction can place adjacent heading lines in separate blocks, while
    # also combining different columns that share a baseline. Group globally by
    # x-position and close vertical spacing to recover multi-line headings.
    header_lines: list[ParsedElement] = []
    for line_group in _group_lines_by_column(candidate_header_lines):
        line_group = [
            line for line in line_group
            if line.bbox is not None
            and line.bbox.y1 <= table_top
            and table_top - line.bbox.y1 <= _HEADER_SEARCH_DISTANCE
        ]
        for adjacent_group in _split_lines_by_vertical_gap(line_group):
            text = " ".join(
                line.text.strip()
                for line in sorted(adjacent_group, key=lambda item: item.bbox.y0 if item.bbox else 0.0)
                if line.text.strip()
            )
            if text and _header_label(text):
                representative = min(adjacent_group, key=lambda item: item.bbox.y0 if item.bbox else 0.0)
                header_lines.append(
                    ParsedElement(
                        type="line",
                        text=text,
                        bbox=_union_bboxes([line.bbox for line in adjacent_group]),
                        source=representative.source,
                    )
                )

    for line in sorted(header_lines, key=lambda item: item.bbox.y1 if item.bbox else 0):
        label = _header_label(line.text)
        if label is None or line.bbox is None:
            continue
        closest_index = min(range(column_count), key=lambda index: abs(column_centres[index] - (line.bbox.x0 + line.bbox.x1) / 2))
        # Avoid assigning broad headings to an arbitrary nearby column.
        if abs(column_centres[closest_index] - (line.bbox.x0 + line.bbox.x1) / 2) <= 0.08:
            labels[closest_index] = label

    column_texts = [
        [row.children[index].text.strip() for row in rows if index < len(row.children)]
        for index in range(column_count)
    ]
    for index, values in enumerate(column_texts):
        if labels[index] is not None:
            continue
        nonempty = [value for value in values if value]
        if not nonempty:
            continue
        if index == 0 and any(sum(char.isalpha() for char in value) >= 5 for value in nonempty):
            labels[index] = "Description"
            continue
        normalized_values = [value.strip().upper().replace(".", "") for value in nonempty]
        if all(value in _CURRENCY_CODES for value in normalized_values):
            labels[index] = "Currency"
            continue
        if all(re.fullmatch(r"\d{6,10}", re.sub(r"\s+", "", value)) for value in nonempty):
            labels[index] = "Tariff code"
            continue
        if all(_looks_like_amount(value) for value in nonempty):
            labels[index] = "Price"

    return [label or f"Column {index + 1}" for index, label in enumerate(labels)]


def _group_lines_by_column(lines: list[ParsedElement]) -> list[list[ParsedElement]]:
    """Group lines that share a column start; vertical runs are split separately."""
    groups: list[list[ParsedElement]] = []
    for line in sorted(
        (line for line in lines if line.text.strip() and line.bbox is not None),
        key=lambda item: (item.bbox.x0 if item.bbox else 0.0, item.bbox.y0 if item.bbox else 0.0),
    ):
        assert line.bbox is not None
        matching = next(
            (
                group
                for group in groups
                if group[0].bbox is not None
                and abs(group[0].bbox.x0 - line.bbox.x0) <= _COLUMN_TOLERANCE
            ),
            None,
        )
        if matching is None:
            groups.append([line])
        else:
            matching.append(line)
    return groups


def _split_lines_by_vertical_gap(lines: list[ParsedElement]) -> list[list[ParsedElement]]:
    """Split same-column lines into nearby runs, avoiding unrelated paragraphs."""
    ordered = sorted(lines, key=lambda item: item.bbox.y0 if item.bbox is not None else 0.0)
    groups: list[list[ParsedElement]] = []
    for line in ordered:
        if line.bbox is None:
            continue
        if not groups or groups[-1][-1].bbox is None or line.bbox.y0 - groups[-1][-1].bbox.y1 > 0.018:
            groups.append([line])
        else:
            groups[-1].append(line)
    return groups


def _looks_like_amount(value: str) -> bool:
    normalized = re.sub(r"\b(?:EUR|USD|GBP|CHF|CAD|AUD|JPY|CNY)\b|[€$£¥]", "", value, flags=re.IGNORECASE)
    normalized = normalized.replace(" ", "").replace("\u00a0", "")
    return bool(re.fullmatch(r"\d+(?:[.,]\d{3})*(?:[.,]\d{2})", normalized))


def _header_label(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    patterns = (
        (r"\b(zolltarifnummer|tariff\s*(?:no\.?|number|code)|hs\s*code)\b", "Tariff code"),
        (r"\b(description|bezeichnung|warenbeschreibung|item description|goods description)\b", "Description"),
        (r"\bquantity\s+shipped\b", "Quantity shipped"),
        (r"\bquantity\s+ordered\b", "Quantity ordered"),
        (r"\b(quantity|qty|menge|pcs)\b", "Quantity"),
        (r"\b(unit\s*price|einzelpreis)\b", "Unit price"),
        (r"\b(total\s*(?:amount|price)|gesamtpreis|gesamtbetrag)\b", "Total amount"),
        (r"\b(currency|währung)\b", "Currency"),
        (r"\b(prices?|preis|amount|wert)\b", "Price"),
        (r"\b(weight|gewicht)\b", "Weight"),
    )
    for pattern, label in patterns:
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            return label
    return None


def _union_bboxes(boxes: list[BBox | None]) -> BBox | None:
    valid = [box for box in boxes if box is not None]
    if not valid:
        return None
    return BBox(
        x0=min(box.x0 for box in valid),
        y0=min(box.y0 for box in valid),
        x1=max(box.x1 for box in valid),
        y1=max(box.y1 for box in valid),
    )


def parse_pdf_document(
    pdf_bytes: bytes,
    max_pages: int = 15,
    ocr_min_confidence: float = 0.3,
    ocr_scans: bool = True,
) -> ParsedDocument:
    """Parse PDF text into a geometry-preserving document model.

    This adapter handles text already present in a PDF text layer, runs positioned OCR
    on full-page scan images, and extracts tables detected by PyMuPDF's ``find_tables``
    or conservative aligned-text heuristics. Text blocks contain line children, lines
    contain positioned words, and tables contain positioned cells. It does not
    reconstruct table structure from image-only/scanned tables.
    """
    if max_pages <= 0:
        raise ValueError(f"max_pages must be positive, got {max_pages}")
    if not 0 <= ocr_min_confidence <= 1:
        raise ValueError(f"ocr_min_confidence must be between 0 and 1, got {ocr_min_confidence}")

    document = _safe_open_pdf(pdf_bytes)
    parsed = ParsedDocument(source_format="pdf")
    if document is None:
        return parsed

    try:
        page_count = min(len(document), max_pages)
        parsed.metadata["page_count"] = len(document)
        parsed.metadata["pages_processed"] = page_count
        parsed.metadata["truncated"] = len(document) > max_pages
        for page_index in range(page_count):
            page = document[page_index]
            rect = page.rect
            elements = _page_elements(
                page,
                ocr_min_confidence=ocr_min_confidence,
                ocr_scans=ocr_scans,
            )
            parsed.pages.append(
                ParsedPage(
                    number=page_index + 1,
                    width=float(rect.width),
                    height=float(rect.height),
                    units="pt",
                    elements=elements,
                    relations=_image_caption_relations(elements),
                )
            )
    finally:
        document.close()

    return parsed
