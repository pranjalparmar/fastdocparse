"""PDF utilities: render pages to images, base64 encoding, box coordinate transforms."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import List, Optional, Tuple

import pymupdf  # PyMuPDF
from PIL import Image

# Rendering constants
PDF_RENDER_DPI = 150


def _safe_open_pdf(pdf_bytes: bytes) -> Optional["pymupdf.Document"]:
    """pymupdf.open() raises pymupdf.FileDataError/EmptyFileError (both RuntimeError
    subclasses, not ValueError) on corrupt or non-PDF bytes — a raw file upload gone
    wrong is an expected, common failure mode here, not a programming error, so it
    shouldn't propagate as an unhandled crash. Returns None on failure; callers treat
    that the same as "no text found," which the rest of the pipeline already handles."""
    try:
        return pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return None


@dataclass
class PageImage:
    """A rendered PDF page."""
    index: int          # 0-based page index
    png_bytes: bytes    # PNG image bytes
    width: int          # image width in pixels
    height: int         # image height in pixels


def pdf_to_page_images(pdf_bytes: bytes, max_pages: int = 3, dpi: int = PDF_RENDER_DPI, max_dim: int = 1536) -> List[PageImage]:
    """Render up to max_pages of a PDF to a PNG image at the given DPI.

    Returns a list of PageImage in page order.
    """
    pages: List[PageImage] = []
    doc = _safe_open_pdf(pdf_bytes)
    if doc is None:
        return pages
    try:
        zoom = dpi / 72.0  # 72 DPI is the PDF default
        matrix = pymupdf.Matrix(zoom, zoom)
        for i in range(min(len(doc), max_pages)):
            page = doc[i]
            pix = page.get_pixmap(matrix=matrix, alpha=False)

            # Convert to PIL Image for resizing. Annotated as the base Image.Image since
            # .resize() below returns that, not the narrower ImageFile.ImageFile that
            # Image.open() does — both work identically for everything used here.
            img: Image.Image = Image.open(io.BytesIO(pix.tobytes("png")))

            # Resize to max_dim for better OCR fidelity
            if img.width > max_dim or img.height > max_dim:
                ratio = min(max_dim / img.width, max_dim / img.height)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            
            # Convert back to bytes
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            png = buf.getvalue()
            
            pages.append(
                PageImage(index=i, png_bytes=png, width=img.width, height=img.height)
            )
    finally:
        doc.close()
    return pages


def image_bytes_to_page_image(img_bytes: bytes) -> PageImage:
    """Convert image bytes (PNG/JPG) to a PageImage.

    The image is loaded, resized for faster processing, and re-encoded as PNG.
    """
    # Annotated as the base Image.Image since .convert()/.resize() below return that,
    # not the narrower ImageFile.ImageFile that Image.open() does.
    img: Image.Image = Image.open(io.BytesIO(img_bytes))
    # Convert to RGB if necessary (for JPEGs with transparency, etc.)
    if img.mode != "RGB":
        img = img.convert("RGB")
    
    # Resize to max 1536x1536 for better OCR fidelity
    max_dim = 1536
    if img.width > max_dim or img.height > max_dim:
        ratio = min(max_dim / img.width, max_dim / img.height)
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
    
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return PageImage(index=0, png_bytes=buf.getvalue(), width=img.width, height=img.height)


def _grid_to_markdown(grid: List[List[str]]) -> str:
    """Convert a 2D table array into a clean Markdown table string."""
    if not grid or not grid[0]:
        return ""
    header = [str(c or "").strip().replace("\n", " ") for c in grid[0]]
    if not any(header):
        return ""
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * len(header)) + " |"]
    for row in grid[1:]:
        row_cells = [str(c or "").strip().replace("\n", " ") for c in row]
        if not any(row_cells):
            continue
        while len(row_cells) < len(header):
            row_cells.append("")
        lines.append("| " + " | ".join(row_cells[:len(header)]) + " |")
    return "\n".join(lines)


def _bbox_overlaps(b1: Tuple[float, float, float, float], b2: Tuple[float, float, float, float], thresh: float = 0.5) -> bool:
    """Check if block b1 overlaps significantly with table bbox b2."""
    x0 = max(b1[0], b2[0])
    y0 = max(b1[1], b2[1])
    x1 = min(b1[2], b2[2])
    y1 = min(b1[3], b2[3])
    if x1 <= x0 or y1 <= y0:
        return False
    inter_area = (x1 - x0) * (y1 - y0)
    b1_area = (b1[2] - b1[0]) * (b1[3] - b1[1])
    return (inter_area / b1_area) > thresh if b1_area > 0 else False


def extract_layout_markdown_from_pdf(pdf_bytes: bytes, max_pages: int = 15, structured_mode: bool = False) -> str:
    """Phase 1 Engine: Extract digital PDF into layout-preserved Markdown text."""
    doc = _safe_open_pdf(pdf_bytes)
    pages_md: List[str] = []
    if doc is None:
        return ""
    try:
        for i in range(min(len(doc), max_pages)):
            page = doc[i]
            
            # 1. Extract tables
            table_bboxes: List[Tuple[float, float, float, float]] = []
            table_markdowns: List[Tuple[float, str]] = []  # (y0, markdown_str)
            try:
                tabs = page.find_tables()
                for tab in tabs.tables:
                    grid = tab.extract()
                    md = _grid_to_markdown(grid)
                    if md:
                        table_bboxes.append(tab.bbox)
                        table_markdowns.append((tab.bbox[1], md))
            except Exception:
                pass

            # 2. Extract text blocks outside tables
            blocks = page.get_text("blocks")
            non_table_blocks: List[Tuple[float, float, str]] = []  # (y0, x0, text)
            for b in blocks:
                if len(b) >= 5 and b[4].strip():
                    bbox = (b[0], b[1], b[2], b[3])
                    # Check overlap with any extracted table
                    if not any(_bbox_overlaps(bbox, tb) for tb in table_bboxes):
                        if structured_mode:
                            non_table_blocks.append((b[1], b[0], f"[X:{int(b[0])}] {b[4].strip()}"))
                        else:
                            non_table_blocks.append((b[1], b[0], b[4].strip()))

            # 3. Merge blocks & tables spatially top-to-bottom
            all_elements: List[Tuple[float, float, str]] = []
            for y0, x0, text in non_table_blocks:
                all_elements.append((y0, x0, text))
            for y0, md in table_markdowns:
                all_elements.append((y0, 0.0, f"\n{md}\n"))

            # Sort by Y position (top-to-bottom) then X position
            all_elements.sort(key=lambda elem: (round(elem[0] / 15), elem[1]))

            page_content = "\n\n".join(text for _, _, text in all_elements)
            if page_content.strip():
                pages_md.append(f"--- PAGE {i + 1} ---\n{page_content}")
    finally:
        doc.close()
    return "\n\n".join(pages_md)


def extract_text_from_pdf(pdf_bytes: bytes, max_pages: int = 15, structured_mode: bool = False) -> str:
    """Extract layout-preserved markdown text from a digital PDF."""
    layout_md = extract_layout_markdown_from_pdf(pdf_bytes, max_pages=max_pages, structured_mode=structured_mode)
    if layout_md.strip():
        return layout_md
    
    # Fallback to plain text
    doc = _safe_open_pdf(pdf_bytes)
    texts: List[str] = []
    if doc is None:
        return ""
    try:
        for i in range(min(len(doc), max_pages)):
            page_text = doc[i].get_text("text")
            if page_text.strip():
                texts.append(f"--- Page {i + 1} ---\n{page_text}")
    finally:
        doc.close()
    return "\n\n".join(texts)


def is_digital_pdf(pdf_bytes: bytes, min_chars: int = 80) -> bool:
    """Return True if the PDF has a readable text layer (i.e. is not a scan)."""
    doc = _safe_open_pdf(pdf_bytes)
    if doc is None:
        return False
    total_chars = 0
    try:
        for i in range(min(len(doc), 3)):
            total_chars += len(doc[i].get_text("text").strip())
            if total_chars >= min_chars:
                return True
    finally:
        doc.close()
    return False


def chunk_document_text(text: str, max_tokens: int = 3000) -> List[str]:
    """
    Split document text into chunks based on page delimiters.
    Approximates tokens via character count (4 chars ~ 1 token).
    """
    max_chars = max_tokens * 4
    pages = text.split("--- PAGE ")
    # The first element might be empty or preamble before the first page.
    # We need to re-attach the delimiter for the rest.
    page_texts = []
    for i, p in enumerate(pages):
        if not p.strip():
            continue
        prefix = "--- PAGE " if i > 0 else ""
        page_texts.append(prefix + p)

    chunks = []
    current_chunk = ""

    for pt in page_texts:
        if len(current_chunk) + len(pt) > max_chars and current_chunk:
            chunks.append(current_chunk.strip())
            current_chunk = pt
        else:
            current_chunk += "\n\n" + pt if current_chunk else pt

    if current_chunk:
        chunks.append(current_chunk.strip())

    # A single page (or a document with no "--- PAGE" delimiters at all) can itself
    # exceed max_chars — the loop above only splits *between* pages, so that chunk
    # would otherwise sail past the configured budget by an arbitrary amount. Hard-split
    # anything still oversized; a mid-sentence cut here is far better than silently
    # blowing an LLM's context window.
    final_chunks = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            final_chunks.append(chunk)
        else:
            for start in range(0, len(chunk), max_chars):
                final_chunks.append(chunk[start:start + max_chars])

    return final_chunks
