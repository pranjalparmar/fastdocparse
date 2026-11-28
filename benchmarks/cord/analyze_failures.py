#!/usr/bin/env python3
"""Generate a reproducible, text-minimizing diagnostic sample from CORD test."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

REPO_SRC = Path(__file__).resolve().parents[2] / "src"
REPO_ROOT = REPO_SRC.parent
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.cord.benchmark_receipts import _annotation_lines, _image_pdf, _predicted_lines
from benchmarks.funsd.benchmark_ocr import _edit_distance, _maximum_iou_matches, _score_document
from fastdocparse.parsing import parse_pdf_document


def _intersection_area(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    return max(0.0, min(left[2], right[2]) - max(left[0], right[0])) * max(
        0.0, min(left[3], right[3]) - max(left[1], right[1])
    )


def _text_error(reference: str, prediction: str) -> float:
    reference = " ".join(reference.split())
    prediction = " ".join(prediction.split())
    return _edit_distance(reference, prediction) / len(reference) if reference else (1.0 if prediction else 0.0)


def diagnose_document(references: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    matches = _maximum_iou_matches(references, predictions, 0.5)
    matched_ref = {ref for ref, _, _ in matches}
    matched_pred = {pred for _, pred, _ in matches}
    ref_text_error = []
    matched_ious = []
    matched_confidences = []
    unmatched_confidences = []
    localization_only = 0
    poor_text_matches = 0
    for ref_index, pred_index, iou in matches:
        error = _text_error(references[ref_index]["text"], predictions[pred_index]["text"])
        ref_text_error.append(error)
        matched_ious.append(iou)
        if predictions[pred_index].get("confidence") is not None:
            matched_confidences.append(predictions[pred_index]["confidence"])
        if error > 0.25:
            poor_text_matches += 1
        if iou < 0.75:
            localization_only += 1

    # A split candidate is one reference line covered by several OCR boxes; a
    # merge candidate is one OCR box covering several reference lines.
    split_candidates = 0
    for ref_index in set(range(len(references))) - matched_ref:
        ref = references[ref_index]["box"]
        area = max(1.0, (ref[2] - ref[0]) * (ref[3] - ref[1]))
        coverages = sorted((_intersection_area(ref, pred["box"]) / area for pred in predictions), reverse=True)
        if len([coverage for coverage in coverages if coverage >= 0.15]) >= 2 and sum(coverages[:3]) >= 0.65:
            split_candidates += 1

    merge_candidates = 0
    for pred_index in set(range(len(predictions))) - matched_pred:
        pred = predictions[pred_index]["box"]
        pred_area = max(1.0, (pred[2] - pred[0]) * (pred[3] - pred[1]))
        covered = sum(_intersection_area(ref["box"], pred) / pred_area >= 0.15 for ref in references)
        if covered >= 2:
            merge_candidates += 1

    unmatched_confidences = [
        predictions[index]["confidence"]
        for index in set(range(len(predictions))) - matched_pred
        if predictions[index].get("confidence") is not None
    ]

    score = _score_document(references, predictions)
    return {
        "reference_lines": len(references),
        "predicted_lines": len(predictions),
        "line_f1_iou_0.50": score["line_iou_0.50"]["f1"],
        "cer_iou_0.50": score["cer_iou_0.50"]["cer"],
        "matched_lines": len(matches),
        "matched_lines_with_text_error_over_25_percent": poor_text_matches,
        "matched_lines_with_iou_0.50_to_0.75": localization_only,
        "reference_lines_missed": len(references) - len(matches),
        "predicted_lines_unmatched": len(predictions) - len(matches),
        "possible_split_lines_among_misses": split_candidates,
        "possible_merged_lines_among_unmatched_predictions": merge_candidates,
        "mean_matched_iou": sum(matched_ious) / len(matched_ious) if matched_ious else None,
        "mean_matched_line_cer": sum(ref_text_error) / len(ref_text_error) if ref_text_error else None,
        "mean_matched_ocr_confidence": statistics.mean(matched_confidences) if matched_confidences else None,
        "mean_unmatched_ocr_confidence": statistics.mean(unmatched_confidences) if unmatched_confidences else None,
        "diagnostic_categories": [
            category
            for condition, category in (
                (poor_text_matches > 0, "recognition_errors_on_spatially_matched_lines"),
                (localization_only > 0, "loose_box_localization"),
                (split_candidates > 0, "possible_line_splitting_or_fragmentation"),
                (merge_candidates > 0, "possible_line_merging_or_over-grouping"),
                (len(references) > len(predictions), "under-detection_or_excessive_line_merging"),
                (len(predictions) > len(references), "over-detection_or_excessive_line-splitting"),
            )
            if condition
        ],
    }


def select_sample(result_path: Path, per_stratum: int = 5) -> dict[str, list[str]]:
    result = json.loads(result_path.read_text(encoding="utf-8"))
    documents = result["per_document"]
    by_cer = sorted(documents, key=lambda key: documents[key]["cer_iou_0.50"]["cer"] or 0.0, reverse=True)
    by_f1 = sorted(documents, key=lambda key: documents[key]["line_iou_0.50"]["f1"])
    by_f1_best = list(reversed(by_f1))
    strata: dict[str, list[str]] = {}
    for label, ordered in (
        ("highest_cer", by_cer),
        ("lowest_line_f1", by_f1),
        ("highest_line_f1", by_f1_best),
    ):
        chosen = []
        for document in ordered:
            if document not in chosen:
                chosen.append(document)
            if len(chosen) >= per_stratum:
                break
        strata[label] = chosen
    return strata


def run(data_dir: Path, benchmark_json: Path, per_stratum: int = 5) -> dict[str, Any]:
    strata = select_sample(benchmark_json, per_stratum)
    selected = list(dict.fromkeys(document for items in strata.values() for document in items))
    diagnostics = {}
    for index, stem in enumerate(selected, 1):
        annotation_path = data_dir / "annotations" / f"{stem}.json"
        image_path = next(
            (
                data_dir / "images" / f"{stem}{suffix}"
                for suffix in (".jpg", ".jpeg", ".png", ".JPG", ".PNG")
                if (data_dir / "images" / f"{stem}{suffix}").is_file()
            ),
            None,
        )
        if image_path is None:
            raise FileNotFoundError(f"No image for CORD record {stem}")
        references = _annotation_lines(json.loads(annotation_path.read_text(encoding="utf-8")))
        pdf_bytes, width, height = _image_pdf(image_path)
        parsed = parse_pdf_document(pdf_bytes, max_pages=1)
        predictions = _predicted_lines(parsed.pages[0].elements if parsed.pages else [], width, height)
        diagnostics[stem] = diagnose_document(references, predictions)
        if index % 5 == 0 or index == len(selected):
            print(f"Analyzed {index}/{len(selected)} selected CORD receipts", file=sys.stderr)

    category_counts: dict[str, int] = {}
    for diagnostic in diagnostics.values():
        for category in diagnostic["diagnostic_categories"]:
            category_counts[category] = category_counts.get(category, 0) + 1
    category_values = {
        key: [diagnostic[key] for diagnostic in diagnostics.values() if diagnostic[key] is not None]
        for key in ("mean_matched_iou", "mean_matched_line_cer", "mean_matched_ocr_confidence", "mean_unmatched_ocr_confidence")
    }
    return {
        "benchmark": "CORD v2 test failure diagnostic",
        "split": "test",
        "sample_selection": {
            "strategy": "up to five each from highest per-document CER, lowest line F1, and highest line F1; deterministic ranking from the saved full test benchmark",
            "strata": strata,
            "unique_documents": len(diagnostics),
        },
        "category_document_counts": category_counts,
        "sample_aggregate": {
            "reference_lines": sum(item["reference_lines"] for item in diagnostics.values()),
            "predicted_lines": sum(item["predicted_lines"] for item in diagnostics.values()),
            "matched_lines": sum(item["matched_lines"] for item in diagnostics.values()),
            "mean_of_document_mean_matched_iou": sum(category_values["mean_matched_iou"]) / len(category_values["mean_matched_iou"]) if category_values["mean_matched_iou"] else None,
            "mean_of_document_mean_matched_line_cer": sum(category_values["mean_matched_line_cer"]) / len(category_values["mean_matched_line_cer"]) if category_values["mean_matched_line_cer"] else None,
            "mean_of_document_mean_matched_ocr_confidence": sum(category_values["mean_matched_ocr_confidence"]) / len(category_values["mean_matched_ocr_confidence"]) if category_values["mean_matched_ocr_confidence"] else None,
            "mean_of_document_mean_unmatched_ocr_confidence": sum(category_values["mean_unmatched_ocr_confidence"]) / len(category_values["mean_unmatched_ocr_confidence"]) if category_values["mean_unmatched_ocr_confidence"] else None,
            "note": "Averages are macro over selected documents, not all 100 receipts.",
        },
        "diagnostic_thresholds": {
            "spatial_match_iou": 0.5,
            "poor_matched_text_error_rate": 0.25,
            "loose_localization_iou_range": "0.50 <= IoU < 0.75",
            "split_merge_coverage_threshold": 0.15,
            "split_merge_minimum_union_coverage": 0.65,
            "note": "split/merge categories are geometric heuristics for manual triage, not ground-truth labels",
        },
        "per_document": diagnostics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="External CORD test export")
    parser.add_argument("--benchmark-json", type=Path, required=True, help="Saved full test benchmark JSON")
    parser.add_argument("--per-stratum", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.data_dir, args.benchmark_json, args.per_stratum)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"sample_selection": result["sample_selection"], "category_document_counts": result["category_document_counts"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
