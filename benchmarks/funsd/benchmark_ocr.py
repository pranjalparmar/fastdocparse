#!/usr/bin/env python3
"""Benchmark fastdocparse's scanned OCR path against FUNSD annotations."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf
from PIL import Image

REPO_SRC = Path(__file__).resolve().parents[2] / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

from fastdocparse.parsing import ParsedElement, parse_pdf_document  # noqa: E402


def _bbox_iou(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    ix0, iy0 = max(left[0], right[0]), max(left[1], right[1])
    ix1, iy1 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def _overlap_ratio(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    overlap = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    min_height = min(left[3] - left[1], right[3] - right[1])
    return overlap / min_height if min_height > 0 else 0.0


def _union(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _group_words_into_lines(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Approximate text lines from word annotations using vertical overlap."""
    ordered = sorted(words, key=lambda item: ((item["box"][1] + item["box"][3]) / 2, item["box"][0]))
    groups: list[list[dict[str, Any]]] = []
    for word in ordered:
        box = word["box"]
        match: list[dict[str, Any]] | None = None
        best_overlap = 0.0
        for group in groups:
            group_box = _union([entry["box"] for entry in group])
            overlap = _overlap_ratio(box, group_box)
            center_delta = abs((box[1] + box[3] - group_box[1] - group_box[3]) / 2)
            max_height = max(box[3] - box[1], group_box[3] - group_box[1])
            if overlap >= 0.5 and center_delta <= max_height * 0.35 and overlap > best_overlap:
                match, best_overlap = group, overlap
        if match is None:
            groups.append([word])
        else:
            match.append(word)

    result = []
    for group in groups:
        group.sort(key=lambda item: item["box"][0])
        result.append({"text": " ".join(item["text"] for item in group), "box": _union([item["box"] for item in group])})
    return sorted(result, key=lambda item: (item["box"][1], item["box"][0]))


def _ground_truth_lines(annotation: dict[str, Any]) -> list[dict[str, Any]]:
    words = []
    for entity in annotation.get("form", []):
        for word in entity.get("words", []):
            text = str(word.get("text", "")).strip()
            box = word.get("box")
            if text and isinstance(box, list) and len(box) == 4:
                words.append({"text": text, "box": tuple(float(value) for value in box)})
    return _group_words_into_lines(words)


def _predicted_lines(elements: list[ParsedElement], width: float, height: float) -> list[dict[str, Any]]:
    result = []
    for element in elements:
        if element.type != "text_block" or element.source != "positioned_ocr" or element.bbox is None:
            continue
        result.append({"text": element.text.strip(), "box": element.bbox.to_source(width, height)})
    return sorted(result, key=lambda item: (item["box"][1], item["box"][0]))


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row]
        for column, right_char in enumerate(right, start=1):
            current.append(min(current[-1] + 1, previous[column] + 1, previous[column - 1] + (left_char != right_char)))
        previous = current
    return previous[-1]


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _maximum_iou_matches(
    references: list[dict[str, Any]], predictions: list[dict[str, Any]], threshold: float
) -> list[tuple[int, int, float]]:
    """Find maximum-cardinality one-to-one matches without extra dependencies."""
    candidates = []
    for ref_index, reference in enumerate(references):
        for pred_index, prediction in enumerate(predictions):
            iou = _bbox_iou(reference["box"], prediction["box"])
            if iou >= threshold:
                candidates.append((ref_index, pred_index, iou))
    by_reference: dict[int, list[tuple[int, float]]] = {}
    for ref_index, pred_index, iou in candidates:
        by_reference.setdefault(ref_index, []).append((pred_index, iou))
    for values in by_reference.values():
        values.sort(key=lambda item: item[1], reverse=True)

    prediction_to_reference: dict[int, tuple[int, float]] = {}

    def augment(ref_index: int, visited: set[int]) -> bool:
        for pred_index, iou in by_reference.get(ref_index, []):
            if pred_index in visited:
                continue
            visited.add(pred_index)
            previous = prediction_to_reference.get(pred_index)
            if previous is None or augment(previous[0], visited):
                prediction_to_reference[pred_index] = (ref_index, iou)
                return True
        return False

    # Constrained references first tends to avoid consuming a prediction that is
    # the only viable match for another reference.
    for ref_index in sorted(range(len(references)), key=lambda index: len(by_reference.get(index, []))):
        augment(ref_index, set())
    return sorted(
        [(ref_index, pred_index, iou) for pred_index, (ref_index, iou) in prediction_to_reference.items()]
    )


def _match_lines(
    references: list[dict[str, Any]], predictions: list[dict[str, Any]], threshold: float
) -> tuple[list[tuple[int, int, float]], set[int], set[int]]:
    matches = _maximum_iou_matches(references, predictions, threshold)
    used_references = {ref_index for ref_index, _, _ in matches}
    used_predictions = {pred_index for _, pred_index, _ in matches}
    return matches, used_references, used_predictions


def _score_document(references: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    scores = {}
    matches_at_05, used_ref_05, used_pred_05 = _match_lines(references, predictions, 0.5)
    for threshold in (0.5, 0.75):
        matches, used_ref, used_pred = _match_lines(references, predictions, threshold)
        tp = len(matches)
        fp, fn = len(predictions) - tp, len(references) - tp
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores[f"line_iou_{threshold:.2f}"] = {
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
        }

    character_errors = 0
    reference_characters = 0
    for ref_index, pred_index, _ in matches_at_05:
        reference_text = _normalize_text(references[ref_index]["text"])
        prediction_text = _normalize_text(predictions[pred_index]["text"])
        character_errors += _edit_distance(reference_text, prediction_text)
        reference_characters += len(reference_text)
    for index, line in enumerate(references):
        if index not in used_ref_05:
            reference_text = _normalize_text(line["text"])
            character_errors += len(reference_text)
            reference_characters += len(reference_text)
    for index, line in enumerate(predictions):
        if index not in used_pred_05:
            character_errors += len(_normalize_text(line["text"]))
    scores["cer_iou_0.50"] = {
        "character_errors": character_errors,
        "reference_characters": reference_characters,
        "cer": character_errors / reference_characters if reference_characters else None,
    }
    scores["line_counts"] = {"reference": len(references), "prediction": len(predictions)}
    return scores


def _aggregate(document_scores: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate: dict[str, Any] = {}
    for metric in ("line_iou_0.50", "line_iou_0.75"):
        total = Counter()
        for score in document_scores:
            total.update({key: score[metric][key] for key in ("true_positive", "false_positive", "false_negative")})
        tp, fp, fn = total["true_positive"], total["false_positive"], total["false_negative"]
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        aggregate[metric] = {
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
            **dict(total),
        }
    errors = sum(score["cer_iou_0.50"]["character_errors"] for score in document_scores)
    characters = sum(score["cer_iou_0.50"]["reference_characters"] for score in document_scores)
    aggregate["cer_iou_0.50"] = {
        "character_errors": errors,
        "reference_characters": characters,
        "cer": errors / characters if characters else None,
    }
    cer_documents = [score["cer_iou_0.50"]["cer"] for score in document_scores if score["cer_iou_0.50"]["cer"] is not None]
    aggregate["document_macro_f1_iou_0.50"] = sum(score["line_iou_0.50"]["f1"] for score in document_scores) / len(document_scores)
    aggregate["document_macro_cer_iou_0.50"] = sum(cer_documents) / len(cer_documents) if cer_documents else None
    aggregate["line_counts"] = {
        key: sum(score["line_counts"][key] for score in document_scores)
        for key in ("reference", "prediction")
    }
    return aggregate


def _image_pdf(image_path: Path) -> tuple[bytes, int, int]:
    with Image.open(image_path) as image:
        width, height = image.size
    pdf = pymupdf.open()
    page = pdf.new_page(width=width, height=height)
    page.insert_image(page.rect, filename=str(image_path), keep_proportion=False)
    data = pdf.tobytes()
    pdf.close()
    return data, width, height


def run_benchmark(data_dir: Path, split: str, limit: int | None = None) -> dict[str, Any]:
    split_dir = data_dir / split
    annotation_dir, image_dir = split_dir / "annotations", split_dir / "images"
    if not annotation_dir.is_dir() or not image_dir.is_dir():
        raise FileNotFoundError(f"Expected annotations/ and images/ under {split_dir}")
    annotation_paths = sorted(annotation_dir.glob("*.json"))
    if limit is not None:
        annotation_paths = annotation_paths[:limit]
    if not annotation_paths:
        raise ValueError(f"No annotation JSON files found in {annotation_dir}")

    per_document = {}
    for index, annotation_path in enumerate(annotation_paths, start=1):
        image_path = image_dir / f"{annotation_path.stem}.png"
        if not image_path.is_file():
            raise FileNotFoundError(f"Missing image for {annotation_path.name}: {image_path}")
        annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
        pdf_bytes, width, height = _image_pdf(image_path)
        parsed = parse_pdf_document(pdf_bytes, max_pages=1)
        elements = parsed.pages[0].elements if parsed.pages else []
        references = _ground_truth_lines(annotation)
        predictions = _predicted_lines(elements, width, height)
        per_document[annotation_path.stem] = _score_document(references, predictions)
        if index % 10 == 0 or index == len(annotation_paths):
            print(f"Processed {index}/{len(annotation_paths)} FUNSD pages", file=sys.stderr)

    return {
        "benchmark": "FUNSD OCR line baseline",
        "split": split,
        "documents": len(per_document),
        "parser": "parse_pdf_document with positioned OCR; no external layout model",
        "runtime": {
            "python": sys.version.split()[0],
            "pymupdf": importlib.metadata.version("pymupdf"),
            "rapidocr_onnxruntime": importlib.metadata.version("rapidocr-onnxruntime"),
        },
        "definitions": {
            "ground_truth_lines": "FUNSD word boxes clustered by vertical overlap (approximate line labels)",
        "line_match": "maximum-cardinality one-to-one bounding-box IoU matching",
            "cer": "whitespace-normalized character error rate; spatial matches at IoU >= 0.50; unmatched reference/prediction text counts as deletion/insertion",
            "not_measured": ["generic layout-region classification", "table-grid reconstruction", "FUNSD semantic entity/relation prediction"],
        },
        "aggregate": _aggregate(list(per_document.values())),
        "per_document": per_document,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="FUNSD root containing testing_data/ and training_data/")
    parser.add_argument("--split", choices=("testing_data", "training_data"), default="testing_data")
    parser.add_argument("--limit", type=int, help="Process only the first N samples (smoke testing)")
    parser.add_argument("--output", type=Path, help="Write detailed JSON results to this path")
    args = parser.parse_args()
    results = run_benchmark(args.data_dir, args.split, args.limit)
    serialized = json.dumps(results, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(json.dumps({"documents": results["documents"], "aggregate": results["aggregate"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
