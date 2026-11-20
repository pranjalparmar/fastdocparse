#!/usr/bin/env python3
"""Score positioned OCR and line grouping against CORD v2 annotations."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image

REPO_SRC = Path(__file__).resolve().parents[2] / "src"
REPO_ROOT = REPO_SRC.parent
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.funsd.benchmark_ocr import _aggregate, _maximum_iou_matches, _score_document  # noqa: E402
from fastdocparse.parsing import ParsedElement, parse_pdf_document  # noqa: E402


def _annotation_lines(annotation: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert CORD valid_line quadrilaterals to line text and envelope boxes."""
    result = []
    for line in annotation.get("valid_line", []):
        words = []
        points = []
        for word in line.get("words", []):
            text = str(word.get("text", "")).strip()
            quad = word.get("quad", {})
            coords = [quad.get(name) for name in ("x1", "y1", "x2", "y2", "x3", "y3", "x4", "y4")]
            if text:
                words.append((text, float(quad.get("x1", 0))))
            if all(value is not None for value in coords):
                points.extend((float(coords[index]), float(coords[index + 1])) for index in (0, 2, 4, 6))
        if not words or not points:
            continue
        words.sort(key=lambda item: item[1])
        result.append({
            "text": " ".join(word for word, _ in words),
            "box": (min(x for x, _ in points), min(y for _, y in points), max(x for x, _ in points), max(y for _, y in points)),
            "group_id": line.get("group_id"),
            "row_id": line.get("row_id"),
            "category": line.get("category"),
        })
    return result


def _predicted_lines(elements: list[ParsedElement], width: int, height: int) -> list[dict[str, Any]]:
    return [
        {
            "text": element.text.strip(),
            "box": element.bbox.to_source(width, height),
            "confidence": element.confidence,
        }
        for element in elements
        if element.type == "text_block" and element.source == "positioned_ocr" and element.bbox is not None
    ]


def _image_pdf(image_path: Path) -> tuple[bytes, int, int]:
    with Image.open(image_path) as image:
        width, height = image.size
    document = pymupdf.open()
    page = document.new_page(width=width, height=height)
    page.insert_image(page.rect, filename=str(image_path), keep_proportion=False)
    payload = document.tobytes()
    document.close()
    return payload, width, height


def _grouping_coverage(references: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure whether OCR line boxes recover annotation-defined word groups."""
    matches = _maximum_iou_matches(references, predictions, 0.5)
    matched_reference_ids = {reference_index for reference_index, _, _ in matches}
    group_ids = {line.get("group_id") for line in references if line.get("group_id") is not None}
    row_ids = {line.get("row_id") for line in references if line.get("row_id") is not None}
    return {
        "reference_lines": len(references),
        "lines_localized_at_iou_0.50": len(matched_reference_ids),
        "line_group_recall_at_iou_0.50": len(matched_reference_ids) / len(references) if references else None,
        "annotation_group_count": len(group_ids),
        "annotation_row_count": len(row_ids),
        "semantic_group_assignment_supported": False,
    }


def run_benchmark(data_dir: Path, split: str, limit: int | None = None) -> dict[str, Any]:
    image_dir, annotation_dir = data_dir / "images", data_dir / "annotations"
    if not image_dir.is_dir() or not annotation_dir.is_dir():
        raise FileNotFoundError(f"Expected images/ and annotations/ under {data_dir}")
    annotations = sorted(annotation_dir.glob("*.json"))
    if limit is not None:
        annotations = annotations[:limit]
    if not annotations:
        raise ValueError(f"No annotation JSON files found in {annotation_dir}")

    per_document = {}
    grouping = {}
    for index, path in enumerate(annotations, 1):
        image_path = next((image_dir / f"{path.stem}{suffix}" for suffix in (".jpg", ".jpeg", ".png", ".JPG", ".PNG") if (image_dir / f"{path.stem}{suffix}").is_file()), None)
        if image_path is None:
            raise FileNotFoundError(f"No matching image for {path.name}")
        annotation = json.loads(path.read_text(encoding="utf-8"))
        reference_lines = _annotation_lines(annotation)
        pdf_bytes, width, height = _image_pdf(image_path)
        parsed = parse_pdf_document(pdf_bytes, max_pages=1)
        predictions = _predicted_lines(parsed.pages[0].elements if parsed.pages else [], width, height)
        per_document[path.stem] = _score_document(reference_lines, predictions)
        grouping[path.stem] = _grouping_coverage(reference_lines, predictions)
        if index % 10 == 0 or index == len(annotations):
            print(f"Processed {index}/{len(annotations)} CORD receipts", file=sys.stderr)

    return {
        "benchmark": "CORD v2 receipt OCR and line-grouping baseline",
        "dataset": "naver-clova-ix/cord-v2",
        "split": split,
        "documents": len(per_document),
        "parser": "parse_pdf_document with positioned OCR; no receipt-specific grouping model",
        "runtime": {
            "python": sys.version.split()[0],
            "pymupdf": importlib.metadata.version("pymupdf"),
            "rapidocr_onnxruntime": importlib.metadata.version("rapidocr-onnxruntime"),
        },
        "definitions": {
            "line_reference": "CORD valid_line entries; word quadrilaterals are enveloped into axis-aligned boxes",
            "line_match": "maximum-cardinality one-to-one bounding-box IoU matching",
            "cer": "whitespace-normalized CER after IoU >= 0.50 spatial matching; unmatched line text counted as insertions/deletions",
            "grouping": "recall of annotation-defined lines recovered as OCR text blocks at IoU >= 0.50; group_id/row_id semantic assignment is not predicted and is not scored",
            "not_measured": ["receipt key-value extraction", "semantic group_id prediction", "menu row_id prediction"],
            "annotation_caveat": "CORD valid_line is the selected line annotation; OCR detections without a matched valid_line are counted as insertions, including detections on content excluded from valid_line.",
        },
        "aggregate": _aggregate(list(per_document.values())),
        "grouping_aggregate": {
            "annotation_lines": sum(item["reference_lines"] for item in grouping.values()),
            "lines_localized_at_iou_0.50": sum(item["lines_localized_at_iou_0.50"] for item in grouping.values()),
            "line_group_recall_at_iou_0.50": (
                sum(item["lines_localized_at_iou_0.50"] for item in grouping.values())
                / sum(item["reference_lines"] for item in grouping.values())
            ) if sum(item["reference_lines"] for item in grouping.values()) else None,
            "semantic_group_assignment_supported": False,
        },
        "per_document": per_document,
        "grouping_per_document": grouping,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="CORD export with images/ and annotations/")
    parser.add_argument("--split", choices=("test", "validation", "train"), default="test")
    parser.add_argument("--limit", type=int, help="Process only the first N receipts")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_benchmark(args.data_dir, args.split, args.limit)
    serialized = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(json.dumps({"documents": result["documents"], "aggregate": result["aggregate"], "grouping": result["grouping_aggregate"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
