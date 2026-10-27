"""Tests for the built-in cross-check rules and config validation."""

import pytest

from fastdocparse.config import ExtractionConfig
from fastdocparse.grounding import (
    Issue,
    check_substring,
    date_parseable_rule,
    numeric_sum_rule,
)


def test_numeric_sum_rule_flags_mismatch():
    rule = numeric_sum_rule(list_field="items", total_field="total", item_key="amount")
    extracted = {"total": 100.0, "items": [{"amount": 20.0}, {"amount": 50.0}]}
    issues = rule(extracted)
    assert issues is not None
    assert {i.field for i in issues} == {"total", "items"}
    assert all(isinstance(i, Issue) for i in issues)


def test_numeric_sum_rule_passes_within_tolerance():
    rule = numeric_sum_rule(list_field="items", total_field="total", item_key="amount", tolerance=0.01)
    extracted = {"total": 70.005, "items": [{"amount": 20.0}, {"amount": 50.0}]}
    assert rule(extracted) is None


def test_numeric_sum_rule_ignores_missing_data():
    rule = numeric_sum_rule(list_field="items", total_field="total")
    assert rule({"total": None, "items": None}) is None
    assert rule({"total": 100.0, "items": []}) is None


def test_date_parseable_rule_flags_unparseable():
    rule = date_parseable_rule("invoice_date")
    issues = rule({"invoice_date": "not a date"})
    assert issues is not None
    assert issues[0].field == "invoice_date"


def test_date_parseable_rule_accepts_default_formats():
    rule = date_parseable_rule("invoice_date")
    assert rule({"invoice_date": "2023-04-15"}) is None
    assert rule({"invoice_date": "04/15/2023"}) is None


def test_date_parseable_rule_custom_formats():
    rule = date_parseable_rule("invoice_date", formats=["%Y%m%d"])
    assert rule({"invoice_date": "20230415"}) is None
    assert rule({"invoice_date": "2023-04-15"}) is not None


def test_date_parseable_rule_ignores_missing_value():
    rule = date_parseable_rule("invoice_date")
    assert rule({"invoice_date": None}) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_pages": 0},
        {"chunk_max_tokens": -1},
        {"pdf_render_dpi": 0},
        {"max_image_dim": -100},
        {"ocr_min_confidence": 1.5},
        {"ocr_min_confidence": -0.1},
    ],
)
def test_extraction_config_rejects_invalid_values(kwargs):
    with pytest.raises(ValueError):
        ExtractionConfig(**kwargs)


def test_extraction_config_accepts_valid_values():
    config = ExtractionConfig(max_pages=5, chunk_max_tokens=1000, ocr_min_confidence=0.5)
    assert config.max_pages == 5


@pytest.mark.parametrize(
    "value, source",
    [
        ("10", "Total: 1,000.00"),
        ("ABC", "A B C Industries"),
        ("12345", "Ref 123 45 other"),
        ("100", "Invoice 1,00,0"),
        ("2846.25", "Total $2,486.25"),
    ],
)
def test_check_substring_rejects_matches_that_straddle_tokens(value, source):
    assert check_substring(value, source) is False


@pytest.mark.parametrize(
    "value, source",
    [
        ("INV-9011", "Invoice no. INV9011"),
        ("INV9011", "Invoice no. INV-9011"),
        ("INV-9011", "Invoice no. INV 9011"),
        ("ACME Inc", "Issued by ACME, Inc."),
        ("1234", "Order 1234 shipped"),
        ("John Smith", "Signed: John  Smith"),
    ],
)
def test_check_substring_tolerates_punctuation_and_spacing_differences(value, source):
    assert check_substring(value, source) is True


def test_check_substring_tolerates_ocr_that_dropped_spaces_between_words():
    assert check_substring("Tue Aug 25 19:06:18 2026", "Photo TueAug2519:06:182026 camera 3") is True


def test_check_substring_dropped_whitespace_still_needs_token_edges():
    assert check_substring("Main Street", "Xmainstreet Y") is False
    assert check_substring("Main Street", "42 MainStreet Ave") is True


def test_check_substring_passes_numeric_flag_into_lists_and_dicts():
    assert check_substring([1234.5, 99.0], "Totals: 1,234.50 and 99.00", numeric=True) is True
    assert check_substring({"a": 1234.5}, "Total 1,234.50", numeric=True) is True
    assert check_substring([1234.5, 99.0], "Totals: 1,234.50 and 98.00", numeric=True) is False


def test_check_substring_passes_date_flag_into_lists():
    assert check_substring(["2021-04-22"], "Shipped 22Apr2021", date=True) is True
