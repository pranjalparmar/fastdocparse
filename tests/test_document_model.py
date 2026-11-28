from pathlib import Path

import pytest
import pymupdf

from fastdocparse.parsing import (
    BBox,
    ParsedDocument,
    ParsedElement,
    ParsedPage,
    parse_pdf_document,
    parsed_document_to_markdown,
)
from fastdocparse.parsing.pdf import _infer_aligned_tables


def test_bbox_normalizes_to_source_coordinates():
    bbox = BBox(0.1, 0.2, 0.5, 0.6)

    assert bbox.to_source(600, 800) == (60, 160, 300, 480)


def test_bbox_rejects_invalid_coordinates():
    with pytest.raises(ValueError, match="Bounding box"):
        BBox(-0.1, 0, 0.5, 0.5)

    with pytest.raises(ValueError, match="finite"):
        BBox(0, 0, float("inf"), 1)


def test_parsed_document_serializes_nested_positioned_elements():
    cell = ParsedElement(
        type="table_cell",
        text="$42.00",
        bbox=BBox(0.7, 0.4, 0.9, 0.45),
        source="pdf_text",
        confidence=0.99,
        attributes={"row": 1, "column": 2},
    )
    table = ParsedElement(type="table", bbox=BBox(0.1, 0.3, 0.95, 0.7), children=[cell])
    document = ParsedDocument(
        source_format="pdf",
        pages=[ParsedPage(number=1, width=612, height=792, units="pt", elements=[table])],
    )

    assert document.to_dict() == {
        "source_format": "pdf",
        "pages": [
            {
                "number": 1,
                "width": 612,
                "height": 792,
                "units": "pt",
                "elements": [
                    {
                        "type": "table",
                        "text": "",
                        "source": "unknown",
                        "bbox": {"x0": 0.1, "y0": 0.3, "x1": 0.95, "y1": 0.7},
                        "children": [
                            {
                                "type": "table_cell",
                                "text": "$42.00",
                                "source": "pdf_text",
                                "bbox": {"x0": 0.7, "y0": 0.4, "x1": 0.9, "y1": 0.45},
                                "confidence": 0.99,
                                "attributes": {"row": 1, "column": 2},
                            }
                        ],
                    }
                ],
            }
        ],
        "metadata": {},
    }


def test_parsed_page_serializes_relations_only_when_present():
    page = ParsedPage(
        number=1,
        width=100,
        height=200,
        units="pt",
        relations=[{"type": "caption_of", "source_index": 0, "target_index": 1, "confidence": 0.9}],
    )

    assert page.to_dict()["relations"] == [
        {"type": "caption_of", "source_index": 0, "target_index": 1, "confidence": 0.9}
    ]
    assert "relations" not in ParsedPage(number=1, width=100, height=200, units="pt").to_dict()


def test_parsed_document_renders_page_text_and_structured_tables_to_markdown():
    document = ParsedDocument(
        source_format="pdf",
        pages=[
            ParsedPage(
                number=1,
                width=100,
                height=200,
                units="pt",
                elements=[
                    ParsedElement(type="text_block", text="Invoice INV-42", source="pdf_text"),
                    ParsedElement(
                        type="table",
                        source="pdf_layout_inference",
                        attributes={"column_headers": ["Description", "Price"]},
                        children=[
                            ParsedElement(
                                type="table_row",
                                source="pdf_layout_inference",
                                children=[
                                    ParsedElement(type="table_cell", text="Widget", source="pdf_text"),
                                    ParsedElement(type="table_cell", text="$42.00", source="pdf_text"),
                                ],
                            )
                        ],
                    ),
                ],
            )
        ],
    )

    assert parsed_document_to_markdown(document) == (
        "--- PAGE 1 ---\n"
        "Invoice INV-42\n\n"
        "> Inferred table; row grouping and headers may be approximate.\n\n"
        "| Description | Price |\n"
        "| --- | --- |\n"
        "| Widget | $42.00 |"
    )


def test_parsed_document_markdown_can_include_layout_tags_for_list_extraction():
    document = ParsedDocument(
        source_format="pdf",
        pages=[
            ParsedPage(
                number=1,
                width=100,
                height=200,
                units="pt",
                elements=[
                    ParsedElement(
                        type="text_block",
                        text="Item A  $42.00",
                        bbox=BBox(0.2, 0.3, 0.8, 0.35),
                        source="pdf_text",
                    )
                ],
            )
        ],
    )

    assert parsed_document_to_markdown(document, structured_mode=True) == (
        "--- PAGE 1 ---\n[X:20] Item A  $42.00"
    )


def test_page_requires_one_based_page_number_and_dimensions():
    with pytest.raises(ValueError, match="one-based"):
        ParsedPage(number=0, width=100, height=200, units="px")

    with pytest.raises(ValueError, match="positive finite"):
        ParsedPage(number=1, width=0, height=200, units="px")


def test_element_confidence_is_bounded():
    with pytest.raises(ValueError, match="confidence"):
        ParsedElement(type="text", confidence=1.1)


def _make_positioned_pdf() -> tuple[bytes, tuple[float, float, float, float]]:
    pdf = pymupdf.open()
    page = pdf.new_page(width=300, height=400)
    page.insert_text((50, 80), "Invoice INV-42", fontsize=12)
    # PyMuPDF reports text bounds after insertion; use that as the geometry oracle.
    expected_bbox = tuple(page.search_for("INV-42")[0])
    pdf_bytes = pdf.tobytes()
    pdf.close()
    return pdf_bytes, expected_bbox


def test_parse_pdf_document_preserves_page_text_and_word_boxes():
    pdf_bytes, expected_bbox = _make_positioned_pdf()

    document = parse_pdf_document(pdf_bytes)

    assert document.source_format == "pdf"
    assert document.metadata["page_count"] == 1
    assert document.metadata["pages_processed"] == 1
    assert document.metadata["truncated"] is False
    page = document.pages[0]
    assert (page.number, page.width, page.height, page.units) == (1, 300.0, 400.0, "pt")
    block = page.elements[0]
    assert block.type == "text_block"
    assert "Invoice INV-42" in block.text

    word = next(word for line in block.children for word in line.children if word.text == "INV-42")
    assert word.bbox is not None
    actual_bbox = word.bbox.to_source(page.width, page.height)
    assert actual_bbox == pytest.approx(expected_bbox, abs=0.01)


def _make_figure_pdf() -> bytes:
    from io import BytesIO

    from PIL import Image

    image_stream = BytesIO()
    Image.new("RGB", (20, 12), "red").save(image_stream, format="PNG")
    pdf = pymupdf.open()
    page = pdf.new_page(width=300, height=400)
    page.insert_image(pymupdf.Rect(40, 100, 140, 160), stream=image_stream.getvalue())
    page.insert_text((40, 180), "Figure 1: Sample diagram", fontsize=10)
    page.insert_text((40, 250), "Ordinary paragraph, not a caption.", fontsize=10)
    data = pdf.tobytes()
    pdf.close()
    return data


def test_parse_pdf_document_preserves_image_geometry_and_links_explicit_caption():
    parsed = parse_pdf_document(_make_figure_pdf())
    page = parsed.pages[0]
    images = [element for element in page.elements if element.type == "image"]

    assert len(images) == 1
    image = images[0]
    assert image.source == "pdf_image"
    assert image.bbox is not None
    assert image.bbox.to_source(page.width, page.height) == pytest.approx((40, 100, 140, 160))
    assert image.attributes["width_px"] == 20
    assert image.attributes["height_px"] == 12
    assert page.relations == [
        {
            "type": "caption_of",
            "source_index": page.elements.index(image),
            "target_index": next(
                index for index, element in enumerate(page.elements) if element.text == "Figure 1: Sample diagram"
            ),
            "confidence": 0.9,
        }
    ]
    assert any("Ordinary paragraph" in element.text for element in page.elements)


def test_parse_pdf_document_marks_full_page_image_as_scan_not_figure():
    from io import BytesIO

    from PIL import Image

    image_stream = BytesIO()
    Image.new("RGB", (30, 40), "white").save(image_stream, format="PNG")
    pdf = pymupdf.open()
    page = pdf.new_page(width=300, height=400)
    page.insert_image(page.rect, stream=image_stream.getvalue())
    data = pdf.tobytes()
    pdf.close()

    parsed = parse_pdf_document(data)
    images = [element for element in parsed.pages[0].elements if element.type == "image"]

    assert len(images) == 1
    assert images[0].attributes["role"] == "full_page_scan"
    assert parsed.pages[0].relations == []


def test_parse_pdf_document_adds_positioned_ocr_elements_for_scan():
    from unittest.mock import patch

    from io import BytesIO

    from PIL import Image

    image_stream = BytesIO()
    Image.new("RGB", (30, 40), "white").save(image_stream, format="PNG")
    pdf = pymupdf.open()
    page = pdf.new_page(width=300, height=400)
    page.insert_image(page.rect, stream=image_stream.getvalue())
    data = pdf.tobytes()
    pdf.close()

    with patch(
        "fastdocparse.ocr_engine.extract_positioned_text_from_image_ocr",
        return_value=[("Scanned", (20.0, 40.0, 120.0, 80.0), 0.95)],
    ):
        parsed = parse_pdf_document(data)

    page = parsed.pages[0]
    ocr_blocks = [element for element in page.elements if element.source == "positioned_ocr"]
    assert len(ocr_blocks) == 1
    assert ocr_blocks[0].text == "Scanned"
    assert ocr_blocks[0].confidence == pytest.approx(0.95)
    assert ocr_blocks[0].bbox is not None
    assert ocr_blocks[0].bbox.x0 == pytest.approx(20 / 600)
    assert ocr_blocks[0].bbox.y0 == pytest.approx(40 / 800)
    assert ocr_blocks[0].children[0].children[0].text == "Scanned"
    assert not any(
        element.type == "table" and element.source == "pdf_layout_inference"
        for element in page.elements
    )


def test_parse_pdf_document_respects_page_limit_and_handles_invalid_input():
    pdf = pymupdf.open()
    pdf.new_page(width=100, height=200)
    pdf.new_page(width=100, height=200)
    two_page_bytes = pdf.tobytes()
    pdf.close()

    parsed = parse_pdf_document(two_page_bytes, max_pages=1)
    assert len(parsed.pages) == 1
    assert parsed.metadata["page_count"] == 2
    assert parsed.metadata["pages_processed"] == 1
    assert parsed.metadata["truncated"] is True
    assert parse_pdf_document(b"not a pdf").pages == []


def test_parse_pdf_document_rejects_non_positive_page_limit():
    with pytest.raises(ValueError, match="max_pages must be positive"):
        parse_pdf_document(b"anything", max_pages=0)


def _make_table_pdf() -> bytes:
    pdf = pymupdf.open()
    page = pdf.new_page(width=300, height=200)
    page.draw_rect(pymupdf.Rect(20, 30, 220, 100), width=1)
    page.draw_line((120, 30), (120, 100), width=1)
    page.draw_line((20, 65), (220, 65), width=1)
    page.insert_text((30, 50), "Item")
    page.insert_text((130, 50), "Amount")
    page.insert_text((30, 85), "Tea")
    page.insert_text((130, 85), "4.00")
    pdf_bytes = pdf.tobytes()
    pdf.close()
    return pdf_bytes


def test_parse_pdf_document_preserves_table_cells_and_positions():
    parsed = parse_pdf_document(_make_table_pdf())

    elements = parsed.pages[0].elements
    tables = [element for element in elements if element.type == "table"]
    assert len(tables) == 1
    table = tables[0]
    assert table.source == "pdf_table"
    assert table.attributes["row_count"] == 2
    assert table.bbox is not None
    assert table.bbox.to_source(300, 200) == pytest.approx((20, 30, 220, 100))

    cells = table.children
    assert [(cell.attributes["row"], cell.attributes["column"], cell.text) for cell in cells] == [
        (0, 0, "Item"),
        (0, 1, "Amount"),
        (1, 0, "Tea"),
        (1, 1, "4.00"),
    ]
    assert all(cell.bbox is not None for cell in cells)
    assert cells[0].bbox.to_source(300, 200) == pytest.approx((20, 30, 120, 65))
    assert cells[3].bbox.to_source(300, 200) == pytest.approx((120, 65, 220, 100))

    # Table cell contents must not also remain as duplicate free-flow text blocks.
    non_table_text = "\n".join(element.text for element in elements if element.type == "text_block")
    assert "Amount" not in non_table_text
    assert "4.00" not in non_table_text


def test_parse_pdf_document_infers_repeated_unruled_invoice_rows():
    def block(text, x0, y0, x1, y1):
        return ParsedElement(
            type="text_block",
            text=text,
            bbox=BBox(x0, y0, x1, y1),
            source="pdf_text",
        )

    # Model separate positioned blocks as found on the real A10 invoice. The total
    # is one wide block, not separate cells, and should not join the repeated rows.
    elements = [
        block("Item A product description", 0.08, 0.30, 0.35, 0.32),
        block("87085055", 0.60, 0.30, 0.69, 0.32),
        block("1,650.00", 0.85, 0.30, 0.94, 0.32),
        block("Item B product description", 0.08, 0.34, 0.35, 0.36),
        block("87085055", 0.60, 0.34, 0.69, 0.36),
        block("920.00", 0.85, 0.34, 0.94, 0.36),
        block("Total 2,570.00", 0.08, 0.40, 0.94, 0.42),
    ]

    parsed_elements = _infer_aligned_tables(elements)
    tables = [element for element in parsed_elements if element.type == "table"]

    assert len(tables) == 1
    table = tables[0]
    assert table.source == "pdf_layout_inference"
    assert table.attributes == {
        "inferred": True,
        "row_count": 2,
        "column_headers": ["Description", "Tariff code", "Price"],
    }
    assert [row.text for row in table.children] == [
        "Item A product description | 87085055 | 1,650.00",
        "Item B product description | 87085055 | 920.00",
    ]
    assert [len(row.children) for row in table.children] == [3, 3]
    assert all(cell.bbox is not None for row in table.children for cell in row.children)

    remaining_text = "\n".join(element.text for element in parsed_elements if element.type == "text_block")
    assert "Total" in remaining_text
    assert "2,570.00" in remaining_text


def test_aligned_key_value_blocks_are_not_inferred_as_a_table():
    def block(text, x0, y0, x1, y1):
        return ParsedElement(type="text_block", text=text, bbox=BBox(x0, y0, x1, y1), source="pdf_text")

    elements = [
        block("EORI NUMBER", 0.1, 0.3, 0.3, 0.32),
        block("EORI NUMBER", 0.6, 0.3, 0.8, 0.32),
        block("DE3522725", 0.1, 0.34, 0.3, 0.36),
        block("GB227291661000", 0.6, 0.34, 0.8, 0.36),
        block("INCOTERMS", 0.1, 0.38, 0.3, 0.40),
        block("DELIVERY (IF DIFFERENT TO CONSIGNEE)", 0.6, 0.38, 0.9, 0.40),
    ]

    parsed_elements = _infer_aligned_tables(elements)

    assert not any(element.type == "table" for element in parsed_elements)
    assert len([element for element in parsed_elements if element.type == "text_block"]) == 6


def test_aligned_table_inference_does_not_join_distant_page_content():
    def block(text, x0, y0, x1, y1):
        return ParsedElement(type="text_block", text=text, bbox=BBox(x0, y0, x1, y1), source="pdf_text")

    item_rows = [
        block("Item A description", 0.08, 0.30, 0.35, 0.32),
        block("HS87085055 qty 120", 0.60, 0.30, 0.72, 0.32),
        block("Item B description", 0.08, 0.34, 0.35, 0.36),
        block("HS87085055 qty 240", 0.60, 0.34, 0.72, 0.36),
    ]
    # A separate footer-like pair is horizontally aligned but far away vertically.
    footer_rows = [
        block("Supplier registry 812345", 0.08, 0.80, 0.35, 0.82),
        block("Bank account 12345678", 0.60, 0.80, 0.82, 0.82),
        block("Supplier address 987654", 0.08, 0.84, 0.35, 0.86),
        block("Bank reference 87654321", 0.60, 0.84, 0.82, 0.86),
    ]

    inferred = [element for element in _infer_aligned_tables(item_rows + footer_rows) if element.type == "table"]

    assert len(inferred) == 1
    assert [row.children[0].text for row in inferred[0].children] == ["Item A description", "Item B description"]


def test_real_invoice_corpus_inference_regressions():
    corpus = Path(__file__).resolve().parents[1] / "synthetic-customs-docs"
    a10_path = corpus / "A10_Proforma_Differential_AT-UK.pdf"
    a01_path = corpus / "A01_RGR_proforma_exhibition_GB-AT.pdf"
    a03_path = corpus / "A03_Exportrechnung_Fotobox_DE-CH.pdf"
    a05_path = corpus / "A05_Commercial_invoice_Kranichsen_GB.pdf"
    if not all(path.is_file() for path in (a10_path, a01_path, a03_path, a05_path)):
        pytest.skip("Optional synthetic-customs-docs invoice corpus is not present")

    a10 = parse_pdf_document(a10_path.read_bytes())
    a10_tables = [
        element
        for page in a10.pages
        for element in page.elements
        if element.type == "table" and element.source == "pdf_layout_inference"
    ]
    assert len(a10_tables) == 1
    assert a10_tables[0].attributes["column_headers"] == [
        "Description",
        "Tariff code",
        "Currency",
        "Price",
    ]
    inferred_rows = [row.text for row in a10_tables[0].children]
    assert len(inferred_rows) == 2
    assert "87085055" in inferred_rows[0] and "1.650,00" in inferred_rows[0]
    assert "87085055" in inferred_rows[1] and "920,00" in inferred_rows[1]
    assert any(
        element.type == "text_block" and "2.570,00" in element.text
        for page in a10.pages
        for element in page.elements
    )

    # A non-invoice goods receipt has line-item rows whose visual columns are
    # embedded as separate lines inside a single native text block. Inference must
    # reconstruct the column relationships without invoice-specific value rules.
    a04_path = corpus / "A04_Goods_receipt_Altmark_GB.pdf"
    if a04_path.is_file():
        a04 = parse_pdf_document(a04_path.read_bytes())
        a04_tables = [
            element
            for page in a04.pages
            for element in page.elements
            if element.type == "table" and element.source == "pdf_layout_inference"
        ]
        assert len(a04_tables) == 1
        assert len(a04_tables[0].children) == 6
        first_row_cells = a04_tables[0].children[0].children
        assert [cell.text for cell in first_row_cells] == [
            "1840",
            "1840",
            "212 kg",
            "Automotive relays 12 V / 40 A, 5-pin\nHS85364900",
            "1932.00 EUR",
        ]
        assert all(cell.bbox is not None for row in a04_tables[0].children for cell in row.children)

    # This document has a decorative stamp near its signature. Its vertical letter
    # placements were previously misclassified as an eleven-row table.
    a01 = parse_pdf_document(a01_path.read_bytes())
    assert not any(
        element.type == "table" and element.source == "pdf_layout_inference"
        for page in a01.pages
        for element in page.elements
    )

    # The Fotobox invoice has repeated aligned item rows. The commercial invoice's
    # EORI/Incoterms blocks are key-value fields and must not be promoted to a table.
    a03 = parse_pdf_document(a03_path.read_bytes())
    a03_row_count = sum(
        len(element.children)
        for page in a03.pages
        for element in page.elements
        if element.type == "table" and element.source == "pdf_layout_inference"
    )
    assert a03_row_count >= 2

    a05 = parse_pdf_document(a05_path.read_bytes())
    assert not any(
        element.type == "table" and element.source == "pdf_layout_inference"
        for page in a05.pages
        for element in page.elements
    )


def test_goods_receipt_inferred_headers_match_positioned_page_labels():
    corpus = Path(__file__).resolve().parents[1] / "synthetic-customs-docs"
    path = corpus / "A04_Goods_receipt_Altmark_GB.pdf"
    if not path.is_file():
        pytest.skip("Optional synthetic-customs-docs invoice corpus is not present")

    parsed = parse_pdf_document(path.read_bytes())
    table = next(
        element
        for page in parsed.pages
        for element in page.elements
        if element.type == "table" and element.source == "pdf_layout_inference"
    )

    assert table.attributes["column_headers"] == [
        "Quantity ordered",
        "Quantity shipped",
        "Weight",
        "Description",
        "Price",
    ]
