from benchmarks.funsd.benchmark_ocr import _ground_truth_lines, _score_document


def test_ground_truth_words_are_grouped_into_spatial_lines():
    annotation = {
        "form": [
            {
                "words": [
                    {"text": "right", "box": [50, 10, 80, 20]},
                    {"text": "left", "box": [10, 10, 40, 20]},
                    {"text": "next", "box": [10, 28, 40, 38]},
                ]
            }
        ]
    }

    lines = _ground_truth_lines(annotation)

    assert [line["text"] for line in lines] == ["left right", "next"]
    assert lines[0]["box"] == (10.0, 10.0, 80.0, 20.0)


def test_benchmark_scores_spatial_lines_and_text_errors():
    references = [{"text": "Amount due", "box": (0, 0, 100, 20)}]
    predictions = [
        {"text": "Amount due", "box": (0, 0, 100, 20)},
        {"text": "noise", "box": (200, 0, 250, 20)},
    ]

    score = _score_document(references, predictions)

    assert score["line_iou_0.50"]["precision"] == 0.5
    assert score["line_iou_0.50"]["recall"] == 1.0
    assert score["line_iou_0.50"]["true_positive"] == 1
    # The unmatched five-character prediction is counted as insertion error.
    assert score["cer_iou_0.50"]["cer"] == 0.5


def test_benchmark_matching_uses_maximum_cardinality_not_greedy_best_iou():
    references = [
        {"text": "one", "box": (0, 0, 10, 10)},
        {"text": "two", "box": (0, 0, 10, 10)},
    ]
    predictions = [
        {"text": "p1", "box": (0, 0, 10, 10)},
        {"text": "p2", "box": (0, 0, 10, 10)},
    ]

    score = _score_document(references, predictions)

    assert score["line_iou_0.50"]["true_positive"] == 2
    assert score["line_iou_0.50"]["false_positive"] == 0
    assert score["line_iou_0.50"]["false_negative"] == 0
