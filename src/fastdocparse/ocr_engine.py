"""Phase 1 OCR Engine: Fast, layout-aware OCR for images and scanned PDFs."""

from __future__ import annotations

import importlib.util
import io
import logging
import threading

from PIL import Image

logger = logging.getLogger(__name__)

# Whether the package is installed at all — checked via find_spec, which locates the
# module without executing it, so this costs nothing at import time. The actual
# `import rapidocr_onnxruntime` (which pulls in onnxruntime, opencv, numpy — measured
# at ~1.2s) and RapidOCR() construction only happen lazily, on first real OCR call, via
# _get_rapid_ocr() below. Without this, every `import fastdocparse` would eagerly pay
# that cost even for code that never touches OCR (e.g. just building a Schema).
HAS_RAPID_OCR = importlib.util.find_spec("rapidocr_onnxruntime") is not None

_rapid_ocr = None
_init_lock = threading.Lock()
_init_failed = False


def _get_rapid_ocr():
    global _rapid_ocr, _init_failed
    if _rapid_ocr is not None or _init_failed:
        return _rapid_ocr
    with _init_lock:
        if _rapid_ocr is not None or _init_failed:
            return _rapid_ocr
        try:
            from rapidocr_onnxruntime import RapidOCR

            _rapid_ocr = RapidOCR()
        except (ImportError, OSError, RuntimeError, ValueError):
            logger.warning("RapidOCR failed to initialize; OCR extraction will return empty text.", exc_info=True)
            _init_failed = True
    return _rapid_ocr


_ocr_lock = threading.Lock()


def extract_text_from_image_ocr(image_bytes: bytes, structured_mode: bool = False, min_confidence: float = 0.3) -> str:
    """Extract layout-preserved text from an image (PNG/JPG) using local OCR engine.

    Returns a clean, line-grouped text representation preserving horizontal spacing.
    """
    if not HAS_RAPID_OCR:
        return ""

    rapid_ocr = _get_rapid_ocr()
    if rapid_ocr is None:
        return ""

    try:
        # Annotated as the base Image.Image (not the narrower ImageFile.ImageFile that
        # Image.open() returns) since .convert()/.resize() below return the base type —
        # both are handled identically by everything this code does with `img`.
        img: Image.Image = Image.open(io.BytesIO(image_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")

        buf = io.BytesIO()
        img.save(buf, format="JPEG")

        result = _run_rapid_ocr(rapid_ocr, buf.getvalue())
        if not result:
            return ""

        lines: list[tuple[float, float, str]] = []
        for box, text, conf in result:
            conf_val = float(conf) if conf is not None else 0.0
            if conf_val > min_confidence and text and str(text).strip():
                y0 = min(pt[1] for pt in box)
                x0 = min(pt[0] for pt in box)
                lines.append((y0, x0, str(text).strip()))

        lines.sort(key=lambda item: (round(item[0] / 15), item[1]))

        grouped_lines: list[str] = []
        current_y_group = -1
        current_line_parts: list[str] = []

        for y0, x0, text in lines:
            group = round(y0 / 15)
            if structured_mode:
                text_formatted = f"[X:{int(x0)}] {text}"
            else:
                text_formatted = text
                
            if group != current_y_group:
                if current_line_parts:
                    grouped_lines.append("    ".join(current_line_parts))
                current_y_group = group
                current_line_parts = [text_formatted]
            else:
                current_line_parts.append(text_formatted)

        if current_line_parts:
            grouped_lines.append("    ".join(current_line_parts))

        return "\n".join(grouped_lines)
    except (OSError, RuntimeError, TypeError, ValueError):
        return ""


def extract_positioned_text_from_image_ocr(
    image_bytes: bytes,
    min_confidence: float = 0.3,
) -> list[tuple[str, tuple[float, float, float, float], float]]:
    """Return OCR text, pixel-space boxes, and confidence for an image.

    Unlike :func:`extract_text_from_image_ocr`, this keeps each OCR detection's
    geometry so PDF adapters can map it back onto the source page. Coordinates use
    the raster image passed to RapidOCR, in pixels with a top-left origin.
    """
    if not HAS_RAPID_OCR:
        return []

    rapid_ocr = _get_rapid_ocr()
    if rapid_ocr is None:
        return []

    try:
        img: Image.Image = Image.open(io.BytesIO(image_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")
        with _ocr_lock:
            result = _run_rapid_ocr(rapid_ocr, img)
        if not result:
            return []

        positioned: list[tuple[str, tuple[float, float, float, float], float]] = []
        for polygon, text, confidence in result:
            text = str(text or "").strip()
            confidence = float(confidence) if confidence is not None else 0.0
            if not text or confidence < min_confidence:
                continue
            points = [(float(point[0]), float(point[1])) for point in polygon]
            if not points:
                continue
            bbox = (
                min(point[0] for point in points),
                min(point[1] for point in points),
                max(point[0] for point in points),
                max(point[1] for point in points),
            )
            positioned.append(
                (
                    text,
                    (
                        bbox[0],
                        bbox[1],
                        bbox[2],
                        bbox[3],
                    ),
                    confidence,
                )
            )
        return positioned
    except (OSError, RuntimeError, TypeError, ValueError):
        logger.debug("Positioned OCR failed for image.", exc_info=True)
        return []


def _run_rapid_ocr(rapid_ocr, image):
    """Call RapidOCR under the caller's OCR lock, handling its input forms."""
    result, _ = rapid_ocr(image)
    return result
