#!/usr/bin/env python3
"""Explore OCR line-grouping thresholds on CORD validation, without editing parser code."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_SRC = Path(__file__).resolve().parents[2] / "src"
REPO_ROOT = REPO_SRC.parent
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.cord.benchmark_receipts import _annotation_lines, _image_pdf, _score_document
from benchmarks.funsd.benchmark_ocr import _aggregate
from fastdocparse.parsing import ParsedElement, parse_pdf_document


def _raw_ocr_detections(elements: list[ParsedElement], width: int, height: int) -> list[dict[str, Any]]:
    detections = []
    stack = list(elements)
    while stack:
        element = stack.pop()
        stack.extend(element.children)
        if element.type == "word" and element.source == "positioned_ocr" and element.bbox:
            detections.append({
                "text": element.text.strip(),
                "box": element.bbox.to_source(width, height),
                "confidence": element.confidence,
            })
    return sorted(detections, key=lambda item: ((item["box"][1] + item["box"][3]) / 2, item["box"][0]))


def _vertical_overlap_ratio(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    overlap = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    minimum_height = min(left[3] - left[1], right[3] - right[1])
    return overlap / minimum_height if minimum_height > 0 else 0.0


def _union(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)


def group_detections(
    detections: list[dict[str, Any]],
    min_vertical_overlap: float,
    max_horizontal_gap_heights: float,
    max_center_delta_heights: float = 0.5,
) -> list[dict[str, Any]]:
    """Cluster raw OCR detections into rows, preventing distant column jumps."""
    groups: list[list[dict[str, Any]]] = []
    for detection in sorted(detections, key=lambda item: ((item["box"][1] + item["box"][3]) / 2, item["box"][0])):
        best_group = None
        best_overlap = 0.0
        box = detection["box"]
        height = max(1.0, box[3] - box[1])
        for group in groups:
            group_box = _union([item["box"] for item in group])
            group_height = max(1.0, group_box[3] - group_box[1])
            overlap = _vertical_overlap_ratio(box, group_box)
            center_delta = abs((box[1] + box[3] - group_box[1] - group_box[3]) / 2)
            horizontal_gap = max(0.0, box[0] - group_box[2], group_box[0] - box[2])
            allowed_gap = max_horizontal_gap_heights * max(height, group_height)
            if (
                overlap >= min_vertical_overlap
                and center_delta <= max_center_delta_heights * max(height, group_height)
                and horizontal_gap <= allowed_gap
                and overlap > best_overlap
            ):
                best_group, best_overlap = group, overlap
        if best_group is None:
            groups.append([detection])
        else:
            best_group.append(detection)

    lines = []
    for group in groups:
        group.sort(key=lambda item: item["box"][0])
        confidences = [item["confidence"] for item in group if item.get("confidence") is not None]
        lines.append({
            "text": " ".join(item["text"] for item in group if item["text"]),
            "box": _union([item["box"] for item in group]),
            "confidence": sum(confidences) / len(confidences) if confidences else None,
        })
    return sorted(lines, key=lambda item: (item["box"][1], item["box"][0]))


def _evaluate_setting(
    samples: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]],
    overlap: float,
    gap: float,
) -> dict[str, Any]:
    scores = []
    for references, detections in samples:
        scores.append(_score_document(references, group_detections(detections, overlap, gap)))
    aggregate = _aggregate(scores)
    return {
        "min_vertical_overlap": overlap,
        "max_horizontal_gap_in_heights": gap,
        "line_iou_0.50": aggregate["line_iou_0.50"],
        "line_iou_0.75": aggregate["line_iou_0.75"],
        "cer_iou_0.50": aggregate["cer_iou_0.50"],
        "document_macro_f1_iou_0.50": aggregate["document_macro_f1_iou_0.50"],
        "line_counts": aggregate["line_counts"],
    }


def run(data_dir: Path, split: str = "validation", limit: int | None = None) -> dict[str, Any]:
    if split != "validation":
        raise ValueError("Grouping sweep is intentionally restricted to CORD validation; do not tune on test")
    image_dir, annotation_dir = data_dir / "images", data_dir / "annotations"
    annotations = sorted(annotation_dir.glob("*.json"))
    if limit is not None:
        annotations = annotations[:limit]
    if not annotations:
        raise ValueError(f"No annotations in {annotation_dir}")

    samples = []
    for index, annotation_path in enumerate(annotations, 1):
        image_path = next((image_dir / f"{annotation_path.stem}{suffix}" for suffix in (".jpg", ".jpeg", ".png", ".JPG", ".PNG") if (image_dir / f"{annotation_path.stem}{suffix}").is_file()), None)
        if image_path is None:
            raise FileNotFoundError(f"No image for {annotation_path.name}")
        references = _annotation_lines(json.loads(annotation_path.read_text(encoding="utf-8")))
        pdf_bytes, width, height = _image_pdf(image_path)
        parsed = parse_pdf_document(pdf_bytes, max_pages=1)
        elements = parsed.pages[0].elements if parsed.pages else []
        samples.append((references, _raw_ocr_detections(elements, width, height)))
        if index % 10 == 0 or index == len(annotations):
            print(f"Prepared {index}/{len(annotations)} CORD validation receipts", file=sys.stderr)

    settings = [
        _evaluate_setting(samples, overlap, gap)
        for overlap in (0.35, 0.50, 0.65, 0.80)
        for gap in (0.5, 1.0, 2.0, 4.0)
    ]
    # A priori selection metric: maximize corpus-micro line F1 at IoU .50;
    # break ties by lower CER, then tighter horizontal gap.
    settings.sort(key=lambda item: (
        item["line_iou_0.50"]["f1"],
        -(item["cer_iou_0.50"]["cer"] if item["cer_iou_0.50"]["cer"] is not None else float("inf")),
        -item["max_horizontal_gap_in_heights"],
    ), reverse=True)
    return {
        "benchmark": "CORD v2 validation OCR grouping parameter sweep",
        "dataset": "naver-clova-ix/cord-v2",
        "split": split,
        "documents": len(samples),
        "parser": "RapidOCR detections from current parser; post-hoc grouping only; production parser unchanged",
        "selection_policy": "choose the parameter setting with best corpus-micro line F1 at IoU >= 0.50; CER then horizontal gap break ties; report CORD test only once after freezing the setting",
        "reference_line_policy": "CORD valid_line reference; OCR detections outside or not represented by valid_line remain unmatched insertions",
        "parameter_grid": {"vertical_overlap": [0.35, 0.50, 0.65, 0.80], "horizontal_gap_in_detection_heights": [0.5, 1.0, 2.0, 4.0]},
        "best_setting": settings[0],
        "all_settings": settings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--split", choices=("validation",), default="validation")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.data_dir, args.split, args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"documents": result["documents"], "best_setting": result["best_setting"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
