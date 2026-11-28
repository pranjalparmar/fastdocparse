#!/usr/bin/env python3
"""Score scanned table detection and structure output against cTDaR XML labels."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import warnings
import sys
import xml.etree.ElementTree as ET
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

from benchmarks.funsd.benchmark_ocr import _maximum_iou_matches  # noqa: E402
from fastdocparse.parsing import ParsedElement, parse_pdf_document  # noqa: E402

try:
    from shapely.geometry import Polygon
except ImportError as error:  # pragma: no cover - explained at CLI/runtime
    raise SystemExit("cTDaR official polygon scoring requires Shapely; install benchmarks/ctdar/requirements.txt") from error


def _parse_points(value: str | None) -> list[tuple[float, float]]:
    if not value:
        return []
    points = []
    for point in value.split():
        x, separator, y = point.partition(",")
        if separator:
            points.append((float(x), float(y)))
    return points


def _envelope(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    return min(x for x, _ in points), min(y for _, y in points), max(x for x, _ in points), max(y for _, y in points)


def parse_ctdar_annotation(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    tables = []
    for table in root.findall(".//table"):
        table_points = _parse_points(table.find("Coords").get("points") if table.find("Coords") is not None else None)
        table_coords = _envelope(table_points)
        cells = []
        for cell in table.findall("cell"):
            coords_node = cell.find("Coords")
            box = _envelope(_parse_points(coords_node.get("points") if coords_node is not None else None))
            if box is None:
                continue
            cells.append({
                "box": box,
                "points": _parse_points(coords_node.get("points") if coords_node is not None else None),
                "start_row": int(cell.get("start-row", "0")),
                "end_row": _span_end(cell.get("end-row"), cell.get("start-row", "0")),
                "start_column": int(cell.get("start-col", "0")),
                "end_column": _span_end(cell.get("end-col"), cell.get("start-col", "0")),
            })
        if table_coords is not None or cells:
            tables.append({"box": table_coords, "points": table_points, "cells": cells})
    return {"filename": root.get("filename", path.stem), "tables": tables}


def _span_end(end_value: str | None, start_value: str) -> int:
    start = int(start_value)
    if end_value is None or int(end_value) == -1:
        return start
    return int(end_value)


def adjacency_relations(cells: list[dict[str, Any]]) -> set[tuple[int, int, str]]:
    """Reproduce cTDaR's logical-grid nearest-neighbor relation definition."""
    if not cells:
        return set()
    max_row = max(cell["end_row"] for cell in cells)
    max_column = max(cell["end_column"] for cell in cells)
    grid: dict[tuple[int, int], list[int]] = {}
    for cell_index, cell in enumerate(cells):
        for row in range(cell["start_row"], cell["end_row"] + 1):
            for column in range(cell["start_column"], cell["end_column"] + 1):
                grid.setdefault((row, column), []).append(cell_index)

    relations: set[tuple[int, int, str]] = set()
    for row in range(max_row + 1):
        for column in range(max_column):
            for source_index in grid.get((row, column), []):
                adjacent_column = column + 1
                targets = grid.get((row, adjacent_column), [])
                if targets:
                    for target_index in targets:
                        if target_index != source_index:
                            relations.add((source_index, target_index, "horizontal"))
                else:
                    for next_column in range(column + 2, max_column + 1):
                        targets = grid.get((row, next_column), [])
                        if targets:
                            for target_index in targets:
                                relations.add((source_index, target_index, "horizontal"))
                            break
    for column in range(max_column + 1):
        for row in range(max_row):
            for source_index in grid.get((row, column), []):
                adjacent_row = row + 1
                targets = grid.get((adjacent_row, column), [])
                if targets:
                    for target_index in targets:
                        if target_index != source_index:
                            relations.add((source_index, target_index, "vertical"))
                else:
                    for next_row in range(row + 2, max_row + 1):
                        targets = grid.get((next_row, column), [])
                        if targets:
                            for target_index in targets:
                                relations.add((source_index, target_index, "vertical"))
                            break
    return relations


def polygon_iou(left: list[tuple[float, float]], right: list[tuple[float, float]]) -> float:
    """Polygon IoU compatible with the official evaluator's Shapely geometry path."""
    if len(left) < 3 or len(right) < 3:
        return 0.0
    # The public evaluator converts coordinates to numpy arrays with dtype=int.
    left_polygon = Polygon([(int(x), int(y)) for x, y in left]).buffer(0)
    right_polygon = Polygon([(int(x), int(y)) for x, y in right]).buffer(0)
    union_area = left_polygon.union(right_polygon).area
    return left_polygon.intersection(right_polygon).area / union_area if union_area else 0.0


def official_detection_counts(
    references: list[dict[str, Any]], predictions: list[dict[str, Any]], threshold: float
) -> dict[str, int]:
    """cTDaR Track A polygon-IoU table matching (official thresholds: .6-.9)."""
    remaining = list(predictions)
    matched = 0
    for reference in references:
        for prediction in remaining:
            if polygon_iou(reference["points"], prediction["points"]) >= threshold:
                remaining.remove(prediction)
                matched += 1
                break
    return {"matched": matched, "reference": len(references), "prediction": len(predictions)}


def official_structure_counts(
    reference_tables: list[dict[str, Any]], prediction_tables: list[dict[str, Any]], cell_iou_threshold: float,
    table_iou_threshold: float = 0.8,
) -> dict[str, int]:
    """Reproduce cTDaR Track B table matching, cell mapping, and adjacency counts."""
    remaining_predictions = list(prediction_tables)
    remaining_references = list(reference_tables)
    matched_tables = []
    for reference in list(remaining_references):
        for prediction in list(remaining_predictions):
            if polygon_iou(reference["points"], prediction["points"]) >= table_iou_threshold:
                matched_tables.append((reference, prediction))
                remaining_predictions.remove(prediction)
                remaining_references.remove(reference)
                break

    correct_relations = 0
    reference_relations = 0
    prediction_relations = 0
    for reference, prediction in matched_tables:
        ref_cells, pred_cells = reference["cells"], prediction["cells"]
        mapping: dict[int, int] = {}
        for ref_index, ref_cell in enumerate(ref_cells):
            for pred_index, pred_cell in enumerate(pred_cells):
                if polygon_iou(ref_cell["points"], pred_cell["points"]) >= cell_iou_threshold:
                    mapping[ref_index] = pred_index
                    break

        ref_relations = adjacency_relations(ref_cells)
        pred_relations = adjacency_relations(pred_cells)
        reference_relations += len(ref_relations)
        prediction_relations += len(pred_relations)
        mapped_relations = [
            (mapping[source], mapping[target], direction)
            for source, target, direction in ref_relations
            if source in mapping and target in mapping
        ]
        remaining_mapped_relations = list(mapped_relations)
        for predicted_relation in pred_relations:
            if predicted_relation in remaining_mapped_relations:
                correct_relations += 1
                remaining_mapped_relations.remove(predicted_relation)

    reference_relations += sum(len(adjacency_relations(table["cells"])) for table in remaining_references)
    prediction_relations += sum(len(adjacency_relations(table["cells"])) for table in remaining_predictions)
    return {
        "matched_tables": len(matched_tables),
        "reference_relations": reference_relations,
        "prediction_relations": prediction_relations,
        "correct_relations": correct_relations,
    }


def _counts_to_metrics(counts: dict[str, int], reference_key: str, prediction_key: str) -> dict[str, Any]:
    correct = counts.get("matched", counts.get("correct_relations", 0))
    reference = counts.get("reference", counts.get("reference_relations", 0))
    prediction = counts.get("prediction", counts.get("prediction_relations", 0))
    precision = correct / prediction if prediction else None
    recall = correct / reference if reference else (0.0 if prediction else None)
    if correct == 0 and (reference or prediction):
        f1 = 0.0
    elif precision is not None and recall is not None and precision + recall:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = None
    return {"precision": precision, "recall": recall, "f1": f1, **counts}


def _flatten(elements: list[ParsedElement], kind: str) -> list[ParsedElement]:
    output = []
    for element in elements:
        if element.type == kind:
            output.append(element)
        output.extend(_flatten(element.children, kind))
    return output


def _score(references: list[dict[str, Any]], predictions: list[tuple[float, float, float, float]], threshold: float) -> dict[str, Any]:
    ref_wrapped = [{"box": reference["box"]} for reference in references]
    pred_wrapped = [{"box": prediction} for prediction in predictions]
    matches = _maximum_iou_matches(ref_wrapped, pred_wrapped, threshold)
    tp, fp, fn = len(matches), len(predictions) - len(matches), len(references) - len(matches)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "iou_threshold": threshold,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
    }


def _image_pdf(image_path: Path) -> tuple[bytes, int, int]:
    with warnings.catch_warnings():
        # cTDaR contains intentionally large archival images; those are expected
        # benchmark inputs rather than untrusted user uploads.
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        with Image.open(image_path) as image:
            width, height = image.size
    document = pymupdf.open()
    page = document.new_page(width=width, height=height)
    page.insert_image(page.rect, filename=str(image_path), keep_proportion=False)
    payload = document.tobytes()
    document.close()
    return payload, width, height


def run_benchmark(data_dir: Path, subset: str, limit: int | None = None) -> dict[str, Any]:
    ground_truth = data_dir / subset / "ground_truth"
    if not ground_truth.is_dir():
        raise FileNotFoundError(f"Expected {ground_truth}")
    annotations = sorted(ground_truth.glob("*.xml"))
    if limit is not None:
        annotations = annotations[:limit]
    if not annotations:
        raise ValueError(f"No XML annotations found under {ground_truth}")

    docs = []
    for index, annotation_path in enumerate(annotations, 1):
        annotation = parse_ctdar_annotation(annotation_path)
        image_path = next((ground_truth / f"{annotation_path.stem}{suffix}" for suffix in (".jpg", ".JPG", ".png", ".PNG", ".tif", ".tiff") if (ground_truth / f"{annotation_path.stem}{suffix}").is_file()), None)
        if image_path is None:
            raise FileNotFoundError(f"No image beside {annotation_path}")
        pdf_bytes, width, height = _image_pdf(image_path)
        parsed = parse_pdf_document(pdf_bytes, max_pages=1)
        elements = parsed.pages[0].elements if parsed.pages else []
        page_tables = _flatten(elements, "table")
        table_predictions = [element.bbox.to_source(width, height) for element in page_tables if element.bbox]
        structured_predictions = []
        predicted_cells = []
        for table in page_tables:
            table_cells = [
                {
                    "box": cell.bbox.to_source(width, height),
                    "points": [
                        (cell.bbox.to_source(width, height)[0], cell.bbox.to_source(width, height)[1]),
                        (cell.bbox.to_source(width, height)[2], cell.bbox.to_source(width, height)[1]),
                        (cell.bbox.to_source(width, height)[2], cell.bbox.to_source(width, height)[3]),
                        (cell.bbox.to_source(width, height)[0], cell.bbox.to_source(width, height)[3]),
                    ],
                    "row": cell.attributes.get("row"),
                    "column": cell.attributes.get("column"),
                    "row_span": cell.attributes.get("row_span", 1),
                    "column_span": cell.attributes.get("column_span", 1),
                }
                for cell in _flatten(table.children, "table_cell")
                if cell.bbox
            ]
            predicted_cells.extend(table_cells)
            if table.bbox:
                table_box = table.bbox.to_source(width, height)
                structured_predictions.append({
                    "box": table_box,
                    "points": [(table_box[0], table_box[1]), (table_box[2], table_box[1]), (table_box[2], table_box[3]), (table_box[0], table_box[3])],
                    "cells": table_cells,
                })

        gt_tables = [table for table in annotation["tables"] if table["box"]]
        gt_cells = [cell for table in annotation["tables"] for cell in table["cells"]]
        # Cell polygons are converted to axis-aligned envelopes. This is a simple
        # geometric diagnostic, not the official cTDaR adjacency-relation metric.
        table_score = _score([{"box": table["box"]} for table in gt_tables], table_predictions, 0.5)
        cell_score = _score(gt_cells, [cell["box"] for cell in predicted_cells], 0.5)
        detection_by_threshold = {
            str(threshold): official_detection_counts(gt_tables, structured_predictions, threshold)
            for threshold in (0.6, 0.7, 0.8, 0.9)
        }
        structure_by_threshold = {
            str(threshold): official_structure_counts(annotation["tables"], structured_predictions, threshold)
            for threshold in (0.6, 0.7, 0.8, 0.9)
        }
        matches = _maximum_iou_matches(
            [{"box": cell["box"]} for cell in gt_cells],
            [{"box": cell["box"]} for cell in predicted_cells],
            0.5,
        )
        exact_grid_matches = sum(
            (
                gt_cells[ref_index]["start_row"],
                gt_cells[ref_index]["end_row"],
                gt_cells[ref_index]["start_column"],
                gt_cells[ref_index]["end_column"],
            )
            == (
                predicted_cells[pred_index]["row"],
                predicted_cells[pred_index]["row"] + predicted_cells[pred_index]["row_span"] - 1
                if predicted_cells[pred_index]["row"] is not None else None,
                predicted_cells[pred_index]["column"],
                predicted_cells[pred_index]["column"] + predicted_cells[pred_index]["column_span"] - 1
                if predicted_cells[pred_index]["column"] is not None else None,
            )
            for ref_index, pred_index, _ in matches
        )
        docs.append({
            "document": annotation_path.stem,
            "reference_tables": len(gt_tables),
            "predicted_tables": len(table_predictions),
            "table_detection_iou_0.50": table_score,
            "reference_cells": len(gt_cells),
            "predicted_cells": len(predicted_cells),
            "cell_envelope_iou_0.50": cell_score,
            "exact_grid_coordinates": {
                "matched_cells": len(matches),
                "exact_span_matches": exact_grid_matches,
                "note": "Current parser output has row/column attributes but no predictions exist for scanned cTDaR images; spans are included for completeness.",
            },
            "official_detection_counts_by_iou": detection_by_threshold,
            "official_structure_relation_counts_by_cell_iou": structure_by_threshold,
        })
        if index % 5 == 0 or index == len(annotations):
            print(f"Processed {index}/{len(annotations)} cTDaR documents", file=sys.stderr)

    summed_tables = sum(doc["reference_tables"] for doc in docs)
    summed_cells = sum(doc["reference_cells"] for doc in docs)
    summed_pred_tables = sum(doc["predicted_tables"] for doc in docs)
    summed_pred_cells = sum(doc["predicted_cells"] for doc in docs)
    matched_tables = sum(doc["table_detection_iou_0.50"]["true_positive"] for doc in docs)
    matched_cells = sum(doc["cell_envelope_iou_0.50"]["true_positive"] for doc in docs)
    table_precision = matched_tables / summed_pred_tables if summed_pred_tables else 0.0
    table_recall = matched_tables / summed_tables if summed_tables else None
    cell_precision = matched_cells / summed_pred_cells if summed_pred_cells else 0.0
    cell_recall = matched_cells / summed_cells if summed_cells else None
    official_detection_aggregates = {}
    for threshold in (0.6, 0.7, 0.8, 0.9):
        key = str(threshold)
        totals = {
            field: sum(doc["official_detection_counts_by_iou"][key][field] for doc in docs)
            for field in ("matched", "reference", "prediction")
        }
        official_detection_aggregates[key] = _counts_to_metrics(totals, "reference", "prediction")

    official_structure_aggregates = {}
    for threshold in (0.6, 0.7, 0.8, 0.9):
        key = str(threshold)
        totals = {
            field: sum(doc["official_structure_relation_counts_by_cell_iou"][key][field] for doc in docs)
            for field in ("matched_tables", "reference_relations", "prediction_relations", "correct_relations")
        }
        official_structure_aggregates[key] = _counts_to_metrics(totals, "reference_relations", "prediction_relations")

    return {
        "benchmark": "cTDaR scanned table structure baseline",
        "dataset": "ICDAR 2019 cTDaR public training annotation repository",
        "subset": subset,
        "documents": len(docs),
        "parser": "parse_pdf_document on one-page image-only PDFs; no table recognition model",
        "runtime": {
            "python": sys.version.split()[0],
            "pymupdf": importlib.metadata.version("pymupdf"),
            "rapidocr_onnxruntime": importlib.metadata.version("rapidocr-onnxruntime"),
            "shapely": importlib.metadata.version("shapely"),
        },
        "definitions": {
            "table_detection": "diagnostic axis-aligned-envelope IoU at 0.50 plus cTDaR official polygon-IoU thresholds 0.6/0.7/0.8/0.9",
            "cell_structure": "one-to-one axis-aligned-envelope IoU matching at threshold 0.50",
            "official_track_b_structure": "reimplementation of public cTDaR evaluator: table matching at polygon IoU >= 0.8; match cells at each reported IoU; score directed horizontal/vertical adjacency relation precision and recall",
            "grid": "cell start/end row and column spans are parsed; exact span agreement is additionally reported on geometrically matched cells",
            "limitations": ["These are public training/development annotations, not a held-out official test set", "The current parser emits no scanned-table regions or cells, so Track B relation precision is undefined (no predicted relations) and recall is zero", "Diagnostic envelope metrics are not the official cTDaR polygon metric"],
        },
        "aggregate": {
            "table_detection_iou_0.50": {"recall": table_recall, "precision": table_precision, "f1": (2 * table_precision * table_recall / (table_precision + table_recall) if table_recall is not None and table_precision + table_recall else 0.0), "reference_tables": summed_tables, "predicted_tables": summed_pred_tables, "matched": matched_tables},
            "cell_envelope_iou_0.50": {"recall": cell_recall, "precision": cell_precision, "f1": (2 * cell_precision * cell_recall / (cell_precision + cell_recall) if cell_recall is not None and cell_precision + cell_recall else 0.0), "reference_cells": summed_cells, "predicted_cells": summed_pred_cells, "matched": matched_cells},
            "grid_span_exact_match": {"matched_cells": sum(doc["exact_grid_coordinates"]["matched_cells"] for doc in docs), "exact_span_matches": sum(doc["exact_grid_coordinates"]["exact_span_matches"] for doc in docs)},
            "official_detection_by_polygon_iou": official_detection_aggregates,
            "official_structure_adjacency_relations_by_cell_iou": official_structure_aggregates,
        },
        "per_document": docs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="cTDaR repository root with training/TRACKB1/ground_truth etc.")
    parser.add_argument("--subset", choices=("training/TRACKA", "training/TRACKB1"), default="training/TRACKB1")
    parser.add_argument("--limit", type=int, help="Process only first N docs")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_benchmark(args.data_dir, args.subset, args.limit)
    serialized = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(json.dumps({"documents": result["documents"], "aggregate": result["aggregate"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
