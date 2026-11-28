from pathlib import Path

from benchmarks.cord.benchmark_receipts import _annotation_lines, _grouping_coverage
from benchmarks.cord.benchmark_grouping_sweep import group_detections
from benchmarks.ctdar.benchmark_tables import (
    adjacency_relations,
    _counts_to_metrics,
    official_detection_counts,
    official_structure_counts,
    parse_ctdar_annotation,
)


def test_cord_annotation_quad_is_converted_to_line_box_and_text():
    annotation = {
        "valid_line": [
            {
                "group_id": 3,
                "row_id": 10,
                "category": "menu.nm",
                "words": [
                    {"text": "Coffee", "quad": {"x1": 20, "y1": 5, "x2": 50, "y2": 5, "x3": 50, "y3": 15, "x4": 20, "y4": 15}},
                    {"text": "Large", "quad": {"x1": 55, "y1": 5, "x2": 80, "y2": 5, "x3": 80, "y3": 15, "x4": 55, "y4": 15}},
                ],
            }
        ]
    }

    lines = _annotation_lines(annotation)

    assert len(lines) == 1
    assert lines[0]["text"] == "Coffee Large"
    assert lines[0]["box"] == (20.0, 5.0, 80.0, 15.0)
    assert lines[0]["group_id"] == 3


def test_cord_grouping_metric_does_not_claim_semantic_group_assignment():
    references = [{"text": "Coffee", "box": (0, 0, 10, 10), "group_id": 4, "row_id": 5}]
    predictions = [{"text": "Coffee", "box": (0, 0, 10, 10)}]

    result = _grouping_coverage(references, predictions)

    assert result["line_group_recall_at_iou_0.50"] == 1.0
    assert result["annotation_group_count"] == 1
    assert result["semantic_group_assignment_supported"] is False


def test_ctdar_xml_annotation_includes_table_and_cell_span(tmp_path: Path):
    annotation = tmp_path / "sample.xml"
    annotation.write_text(
        """<document filename="sample.jpg"><table>
        <Coords points="0,0 100,0 100,50 0,50" />
        <cell start-row="0" end-row="1" start-col="0" end-col="0">
          <Coords points="0,0 50,0 50,50 0,50" />
        </cell>
        </table></document>""",
        encoding="utf-8",
    )

    parsed = parse_ctdar_annotation(annotation)

    assert parsed["filename"] == "sample.jpg"
    assert parsed["tables"][0]["box"] == (0.0, 0.0, 100.0, 50.0)
    assert parsed["tables"][0]["cells"][0]["end_row"] == 1
    assert parsed["tables"][0]["cells"][0]["end_column"] == 0


def test_ctdar_uses_minus_one_span_as_single_cell_and_builds_adjacencies():
    cells = [
        {"start_row": 0, "end_row": 0, "start_column": 0, "end_column": 0},
        {"start_row": 0, "end_row": 0, "start_column": 2, "end_column": 2},
        {"start_row": 1, "end_row": 1, "start_column": 0, "end_column": 2},
    ]

    relations = adjacency_relations(cells)

    assert (0, 1, "horizontal") in relations
    assert (0, 2, "vertical") in relations
    assert (1, 2, "vertical") in relations


def test_ctdar_official_polygon_metrics_match_identical_table_and_relations():
    cell_boxes = [
        {"box": (0, 0, 50, 50), "points": [(0, 0), (50, 0), (50, 50), (0, 50)], "start_row": 0, "end_row": 0, "start_column": 0, "end_column": 0},
        {"box": (50, 0, 100, 50), "points": [(50, 0), (100, 0), (100, 50), (50, 50)], "start_row": 0, "end_row": 0, "start_column": 1, "end_column": 1},
    ]
    table = {"box": (0, 0, 100, 50), "points": [(0, 0), (100, 0), (100, 50), (0, 50)], "cells": cell_boxes}

    detection = official_detection_counts([table], [table], 0.6)
    structure = official_structure_counts([table], [table], 0.5)

    assert detection == {"matched": 1, "reference": 1, "prediction": 1}
    assert structure["matched_tables"] == 1
    assert structure["reference_relations"] == 1
    assert structure["prediction_relations"] == 1
    assert structure["correct_relations"] == 1


def test_ctdar_zero_prediction_precision_is_undefined_but_recall_is_zero():
    metrics = _counts_to_metrics(
        {"reference_relations": 4, "prediction_relations": 0, "correct_relations": 0},
        "reference_relations",
        "prediction_relations",
    )

    assert metrics["precision"] is None
    assert metrics["recall"] == 0
    assert metrics["f1"] == 0


def test_cord_posthoc_grouping_joins_same_row_but_not_distant_rows():
    detections = [
        {"text": "Coffee", "box": (0, 0, 30, 10), "confidence": 0.9},
        {"text": "2.50", "box": (45, 1, 65, 11), "confidence": 0.8},
        {"text": "Tea", "box": (0, 20, 25, 30), "confidence": 0.95},
    ]

    lines = group_detections(detections, min_vertical_overlap=0.5, max_horizontal_gap_heights=2)

    assert [line["text"] for line in lines] == ["Coffee 2.50", "Tea"]
