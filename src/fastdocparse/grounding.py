"""Grounding and validation checks for extracted data."""

import logging
import re
import signal
import threading
from datetime import datetime
from typing import Any, Dict, List, Callable, Optional
from dataclasses import dataclass
from .schema import Schema

logger = logging.getLogger(__name__)


def _is_present(value: Any) -> bool:
    """False for None and for empty/whitespace-only strings — both mean "nothing was
    really extracted here," which a bare `value is None` check misses. A required field
    that comes back "" must not silently pass as present (and must not be trivially
    "grounded" either, since an empty string is a substring of anything)."""
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


class _PatternTimeout(Exception):
    pass


def _regex_matches_with_timeout(pattern: str, value: str, timeout: float = 1.0) -> Optional[bool]:
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


CrossCheckRule = Callable[[Dict[str, Any]], Optional[List[Issue]]]


_NUMBER_RE = re.compile(r'-?\d[\d,]*\.?\d*')


def _as_number(value: Any) -> Optional[float]:
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


def _extract_numbers(text: str) -> List[float]:
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
            return datetime.strptime(text, fmt).date()
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
        return all(check_substring(item, source_text) for item in value)

    if isinstance(value, dict):
        if not value:
            return True
        return all(check_substring(v, source_text) for v in value.values())

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

    # Fuzzy match ignoring non-alphanumeric chars
    val_clean = re.sub(r'\W+', '', val_str)
    source_clean = re.sub(r'\W+', '', source_lower)
    
    if val_clean and val_clean in source_clean:
        return True
        
    return False


def validate_field_constraints(schema: Schema, extracted: Dict[str, Any]) -> List[Issue]:
    """Check schema-declared constraints (required, pattern, enum) against extracted values.

    Runs automatically for every schema, independent of any user-supplied cross_check rules —
    these constraints are properties of the schema itself (any domain), not custom business logic.
    """
    issues: List[Issue] = []
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

    def _rule(extracted: Dict[str, Any]) -> Optional[List[Issue]]:
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


def date_parseable_rule(field_name: str, formats: Optional[List[str]] = None) -> CrossCheckRule:
    """Build a rule flagging field_name when its value doesn't parse as a date in any given format."""
    from datetime import datetime

    candidates = formats or ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"]

    def _rule(extracted: Dict[str, Any]) -> Optional[List[Issue]]:
        val = extracted.get(field_name)
        if val is None:
            return None
        for fmt in candidates:
            try:
                datetime.strptime(str(val), fmt)
                return None
            except ValueError:
                continue
        return [Issue(field=field_name, message=f"'{val}' is not a parseable date")]

    return _rule


def cross_check(schema: Schema, extracted: Dict[str, Any], rules: Optional[List[CrossCheckRule]]) -> List[Issue]:
    """
    Run custom cross-check rules on the extracted data.
    Each rule is a callable taking the extracted dict and returning a list of Issues (or None).
    """
    issues: List[Issue] = []
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
