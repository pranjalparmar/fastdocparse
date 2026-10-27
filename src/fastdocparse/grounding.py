"""Grounding and validation checks for extracted data."""
from __future__ import annotations

import logging
import re
import signal
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .schema import Schema

logger = logging.getLogger(__name__)


def _is_present(value: Any) -> bool:
    """False for None and for empty/whitespace-only strings — both mean "nothing was
    really extracted here," which a bare `value is None` check misses. A required field
    that comes back "" must not silently pass as present (and must not be trivially
    "grounded" either, since an empty string is a substring of anything)."""
    if value is None:
        return False
    return not (isinstance(value, str) and not value.strip())


class _PatternTimeout(Exception):
    pass


def _regex_matches_with_timeout(pattern: str, value: str, timeout: float = 1.0) -> bool | None:
    """Guard re.fullmatch against a catastrophically backtracking pattern (e.g.
    "^(a+)+$" against a long near-miss string), since `pattern` is arbitrary user- or
    LLM-supplied regex that can otherwise hang indefinitely.

    Two approaches were tried and rejected before this one:
    - A background *thread* with join(timeout): doesn't work. CPython's `re` engine
      holds the GIL for the whole backtracking search, so a hung regex thread starves
      the main thread too and the timeout never fires (confirmed by testing).
    - A separate *process* with a hard kill: works in isolation, but this application
      loads a native ML runtime at import time (RapidOCR/onnxruntime, for local OCR).
      Spawning a subprocess alongside that runtime crashed the whole process with a
      native SIGABRT on exit ("recursive_mutex lock failed") — confirmed by running it
      through the real CLI, not just a unit test. Not safe here.

    SIGALRM actually interrupts a mid-backtrack regex call in CPython (confirmed by
    testing) and needs no subprocess — but signal handlers only work on the main thread.
    On a non-main thread (e.g. inside aextract()'s background-thread wrapper) or on a
    platform without SIGALRM (Windows), this runs the match with no timeout at all —
    a narrower, documented gap rather than a mechanism that looked safe but wasn't.
    """
    can_use_signal = hasattr(signal, "SIGALRM") and threading.current_thread() is threading.main_thread()

    if not can_use_signal:
        try:
            return re.fullmatch(pattern, value) is not None
        except re.error:
            return None

    def _on_alarm(signum, frame):
        raise _PatternTimeout()

    previous_handler = signal.signal(signal.SIGALRM, _on_alarm)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        return re.fullmatch(pattern, value) is not None
    except _PatternTimeout:
        logger.warning(
            "Pattern check timed out after %.1fs (pattern=%r) — possible catastrophic "
            "backtracking; skipping this field's format check.", timeout, pattern
        )
        return None
    except re.error:
        return None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)

@dataclass
class Issue:
    field: str
    message: str
    severity: str = "warning"
    kind: str = "cross_check"  # "cross_check" | "missing_required" | "invalid_format"


CrossCheckRule = Callable[[dict[str, Any]], Optional[list[Issue]]]


_NUMBER_RE = re.compile(r'-?\d[\d,]*\.?\d*')


def _as_number(value: Any) -> float | None:
    """Parse an int/float or a numeric-looking string (currency symbols, thousands separators) into a float."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r'^[^\d\-]*', '', value.strip()).replace(',', '')
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _extract_numbers(text: str) -> list[float]:
    numbers = []
    for match in _NUMBER_RE.findall(text):
        try:
            numbers.append(float(match.replace(',', '')))
        except ValueError:
            continue
    return numbers


_DATE_CANDIDATE_RE = re.compile(
    r'\d{4}[/-]\d{1,2}[/-]\d{1,2}'           # 2021-04-22, 2021/04/22
    r'|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}'         # 22/04/2021, 04-22-2021
    r'|\d{1,2}\s?[A-Za-z]{3,9}\s?\d{2,4}'     # 22Apr2021, 22 April 2021
    r'|[A-Za-z]{3,9}\s\d{1,2},?\s\d{2,4}'     # April 22, 2021
)

_DATE_FORMATS = [
    "%Y-%m-%d", "%Y/%m/%d",
    "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y",
    "%d%b%Y", "%d %b %Y", "%d%B%Y", "%d %B %Y",
    "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y",
]


def _parse_date_any(text: str):
    """Try each known date format in turn; return a date object or None."""
    text = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).date()
        except ValueError:
            continue
    return None


def check_substring(value: Any, source_text: str, numeric: bool = False, date: bool = False) -> bool:
    """
    Check if the value is grounded (found) in the source text using fuzzy substring matching.
    Returns True if grounded, False otherwise.

    numeric: opt in for fields declared type "number"/"currency" — compares by parsed
    numeric value (tolerant of formatting like "1,234.50" vs 1234.5). Leave False for
    text/ID fields (invoice numbers, container numbers, etc.) where an all-digit value
    is still a string identity, not a number, and "0100" must not be treated as
    equal to "100".

    date: opt in for fields declared type "date" — compares by parsed calendar date, so
    a source document's "22Apr2021" still grounds a normalized "2021-04-22" even though
    the strings don't match at all. Without this, every reformatted-but-correct date gets
    flagged ungrounded, which is a false alarm, not a real hallucination.
    """
    if value is None:
        return True

    if isinstance(value, list):
        if not value:
            return True
        return all(check_substring(item, source_text, numeric=numeric, date=date) for item in value)

    if isinstance(value, dict):
        if not value:
            return True
        return all(check_substring(v, source_text, numeric=numeric, date=date) for v in value.values())

    val_str = str(value).lower()
    source_lower = source_text.lower()

    # Exact lowercase match
    if val_str in source_lower:
        return True

    # Numeric comparison: catches formatting differences (1,234.50 vs 1234.5)
    # that the naive digit-concatenation fallback below gets wrong or right by luck.
    # Opt-in only (see docstring) so digit-string IDs aren't coerced into numbers.
    if numeric:
        num_val = _as_number(value)
        if num_val is not None:
            return any(abs(n - num_val) < 0.01 for n in _extract_numbers(source_text))

    # Date comparison: catches reformatting (22Apr2021 -> 2021-04-22) that no string-based
    # match, fuzzy or otherwise, can see past. Opt-in for the same reason numeric is.
    if date:
        target_date = _parse_date_any(str(value))
        if target_date is not None:
            for candidate in _DATE_CANDIDATE_RE.findall(source_text):
                if _parse_date_any(candidate) == target_date:
                    return True

    value_canon = _canonicalize_numbers(val_str)
    source_canon = _canonicalize_numbers(source_lower)
    return _matches_ignoring_punctuation(value_canon, source_canon) or _matches_across_dropped_whitespace(
        value_canon, source_canon
    )


_NUMBER_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)*")
_US_GROUPED_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_DECIMAL_MARK = "\u01c2"  # a letter that never appears in real documents, so it can't collide with source text


def _canonical_number(token: str) -> str:
    """Rewrite one digit run with its separators resolved: US thousands commas are
    dropped ("1,000.00" -> "1000_00"), any other "." or "," becomes a visible mark
    ("1.2" -> "1_2", "3,5" -> "3_5", "1.000" -> "1_000"). The mark is a word character,
    so later punctuation clean-up can't delete it and merge the digits on either side
    ("1,000.00" must never become "100000", and "1.2" must never become "12"). It is a
    rare Unicode letter rather than "_" so a literal underscore in the text can't pose
    as a decimal point."""
    if _US_GROUPED_RE.fullmatch(token):
        whole, _, fraction = token.replace(",", "").partition(".")
        return whole + (_DECIMAL_MARK + fraction if fraction else "")
    return re.sub(r"[.,]", _DECIMAL_MARK, token)


def _canonicalize_numbers(text: str) -> str:
    return _NUMBER_TOKEN_RE.sub(lambda m: _canonical_number(m.group()), text)


def _punctuation_variants(text: str) -> tuple[str, str]:
    """Two normalizations of text: punctuation deleted ("INV-9011" -> "inv9011") and
    punctuation turned into a separator ("INV-9011" -> "inv 9011"), whitespace collapsed
    in both. A real value can match a source under either reading."""
    # Punctuation between two digits ("1/2", "12-34") is a separator in both readings:
    # deleting it would merge the digits into a different number.
    between_digits = re.sub(r"(?<=\d)[^\w\s]+(?=\d)", " ", text)
    joined = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", between_digits)).strip()
    spaced = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()
    return joined, spaced


def _matches_ignoring_punctuation(value: str, source: str) -> bool:
    """Tolerate punctuation and spacing differences ("ACME Inc" vs "ACME, Inc."), but
    only when the match starts and ends on a token boundary. Without that, "10" would
    ground inside "1,000.00" and "ABC" inside "A B C Industries"."""
    for val_variant in _punctuation_variants(value):
        if not val_variant:
            continue
        pattern = re.compile(r"(?<!\w)" + re.escape(val_variant) + r"(?!\w)")
        for source_variant in _punctuation_variants(source):
            if pattern.search(source_variant):
                return True
    return False


_MIN_SQUASHED_LEN = 6


def _matches_across_dropped_whitespace(value: str, source: str) -> bool:
    """OCR often drops the spaces between words ("TueAug2519:06:182026"). For a
    multi-word value that is long enough that a coincidental match is unlikely, compare
    with all whitespace and punctuation removed, but still require the match to start
    and end on a token edge of the original source text."""
    if len(value.split()) < 2:
        return False
    value_squashed = "".join(c for c in value if c.isalnum())
    if len(value_squashed) < _MIN_SQUASHED_LEN:
        return False

    squashed_chars: list[str] = []
    original_index: list[int] = []
    for i, c in enumerate(source):
        if c.isalnum():
            squashed_chars.append(c)
            original_index.append(i)
    source_squashed = "".join(squashed_chars)

    start = source_squashed.find(value_squashed)
    while start != -1:
        first = original_index[start]
        last = original_index[start + len(value_squashed) - 1]
        starts_on_edge = first == 0 or not source[first - 1].isalnum()
        ends_on_edge = last == len(source) - 1 or not source[last + 1].isalnum()
        if starts_on_edge and ends_on_edge:
            return True
        start = source_squashed.find(value_squashed, start + 1)
    return False


def validate_field_constraints(schema: Schema, extracted: dict[str, Any]) -> list[Issue]:
    """Check schema-declared constraints (required, pattern, enum) against extracted values.

    Runs automatically for every schema, independent of any user-supplied cross_check rules —
    these constraints are properties of the schema itself (any domain), not custom business logic.
    """
    issues: list[Issue] = []
    for f in schema.fields:
        value = extracted.get(f.name)
        is_missing = not _is_present(value) or (f.type == "list" and not value)

        if is_missing:
            if f.required:
                issues.append(Issue(
                    field=f.name,
                    message=f"'{f.name}' is required but was not found in the document",
                    severity="error",
                    kind="missing_required",
                ))
            continue

        if f.type == "list":
            continue  # pattern/enum constraints apply to sub_fields on their own, not implemented here

        if f.pattern and isinstance(value, str) and _regex_matches_with_timeout(f.pattern, value) is False:
            issues.append(Issue(
                field=f.name,
                message=f"'{value}' does not match required pattern {f.pattern!r} for '{f.name}'",
                kind="invalid_format",
            ))

        if f.enum and value not in f.enum:
            issues.append(Issue(
                field=f.name,
                message=f"'{value}' is not one of the allowed values for '{f.name}': {f.enum}",
                kind="invalid_format",
            ))

    return issues


def numeric_sum_rule(list_field: str, total_field: str, item_key: str = "amount", tolerance: float = 0.01) -> CrossCheckRule:
    """Build a rule flagging total_field when it doesn't match the sum of item_key across list_field."""

    def _rule(extracted: dict[str, Any]) -> list[Issue] | None:
        total = extracted.get(total_field)
        items = extracted.get(list_field)
        if total is None or not items:
            return None
        try:
            total_val = float(total)
            calc_val = sum(float(item.get(item_key, 0) or 0) for item in items)
        except (TypeError, ValueError):
            return None
        if abs(calc_val - total_val) > tolerance:
            return [
                Issue(field=total_field, message=f"{total_field} ({total_val}) does not match sum of {list_field}.{item_key} ({calc_val})"),
                Issue(field=list_field, message=f"Sum of {item_key} ({calc_val}) does not match {total_field} ({total_val})"),
            ]
        return None

    return _rule


def date_parseable_rule(field_name: str, formats: list[str] | None = None) -> CrossCheckRule:
    """Build a rule flagging field_name when its value doesn't parse as a date in any given format."""
    candidates = formats or ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"]

    def _rule(extracted: dict[str, Any]) -> list[Issue] | None:
        val = extracted.get(field_name)
        if val is None:
            return None
        for fmt in candidates:
            try:
                datetime.strptime(str(val), fmt).replace(tzinfo=timezone.utc)
                return None
            except ValueError:
                continue
        return [Issue(field=field_name, message=f"'{val}' is not a parseable date")]

    return _rule


def cross_check(schema: Schema, extracted: dict[str, Any], rules: list[CrossCheckRule] | None) -> list[Issue]:
    """
    Run custom cross-check rules on the extracted data.
    Each rule is a callable taking the extracted dict and returning a list of Issues (or None).
    """
    issues: list[Issue] = []
    if not rules:
        return issues

    for rule in rules:
        try:
            result = rule(extracted)
            if result:
                issues.extend(result)
        except Exception:
            logger.exception("Cross-check rule %r raised an exception", getattr(rule, "__name__", rule))
            
    return issues
