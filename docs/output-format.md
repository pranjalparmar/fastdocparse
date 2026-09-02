# Output & Validation

## Result shape

`parser.extract(...)` (and the CLI) return one JSON object with a `_meta` key plus one entry per schema field:

```json
{
  "_meta": {
    "truncated": false,
    "truncation_reason": null
  },
  "invoice_number": {
    "value": "INV-9011",
    "confidence": "high",
    "flags": ["grounded"]
  },
  "total_price": {
    "value": 100.0,
    "confidence": "high",
    "flags": ["grounded"]
  }
}
```

Every field declared in the schema appears in the output, even if nothing was found. Missing data comes back as `"value": null`, never a dropped key.

### `_meta`

| Key | Meaning |
|---|---|
| `truncated` | `true` if the source document had more pages than the configured limit (`ExtractionConfig.max_pages`, default 15) and was cut off. |
| `truncation_reason` | Human-readable explanation, e.g. `"Document is 20 pages long, truncated to 15 pages."` |

### Per-field result

| Key | Meaning |
|---|---|
| `value` | The extracted value, or `null` if not found. |
| `confidence` | `"high"` if grounded in the source text, `"low"` otherwise. |
| `flags` | Zero or more of the flags below. |

## Flags: what each one means and what to do about it

| Flag | Meaning | Suggested action |
|---|---|---|
| `grounded` | The value was found verbatim (or fuzzy-matched, including numeric-format differences like `1,234.50` vs `1234.5`) in the extracted source text. | Trust it. |
| `ungrounded` | A non-null value was returned, but it doesn't appear anywhere in the source text: possible hallucination. | Route to human review before using. |
| `missing_required` | The field is marked `required: true` in the schema, but came back `null`. | Treat as a failed extraction; don't silently proceed. |
| `invalid_format` | The value violates a `pattern` or `enum` constraint declared on the field. | Review. Either the extraction is wrong, or the constraint needs loosening. |
| `failed_check` | A custom cross-check rule (passed via `rules=[...]`) flagged this field. | Depends on the rule, typically a cross-field consistency problem (e.g. totals don't sum). |

A field can carry multiple flags at once, e.g. `["ungrounded", "invalid_format"]`.

**Grounding and constraint checks cost zero extra LLM calls.** They're deterministic string/regex checks against text already extracted from the document.

## Built-in cross-check rules

Two ready-made rules ship with `fastdocparse`, so there's no need to write your own for these common cases:

### `numeric_sum_rule`: flag when a total doesn't match a list's sum

```python
from fastdocparse import numeric_sum_rule

rule = numeric_sum_rule(list_field="line_items", total_field="total_price", item_key="unit_price", tolerance=0.01)
result = parser.extract(document_bytes, schema, rules=[rule])
```

Flags both `total_price` and `line_items` with `failed_check` if `sum(item["unit_price"] for item in line_items)` doesn't match `total_price` within `tolerance`.

### `date_parseable_rule`: flag an unparseable date

```python
from fastdocparse import date_parseable_rule

rule = date_parseable_rule("invoice_date", formats=["%Y-%m-%d"])  # formats optional, sensible defaults included
result = parser.extract(document_bytes, schema, rules=[rule])
```

Flags the field with `failed_check` if its value doesn't match any of the given (or default) date formats.

## Writing a custom rule

A rule is any function `(extracted: dict) -> list[Issue] | None`:

```python
from fastdocparse import Issue

def stock_check(extracted: dict) -> list[Issue] | None:
    qty = extracted.get("quantity")
    if qty is not None and qty > 10000:
        return [Issue(field="quantity", message=f"Quantity {qty} is implausibly high")]
    return None

result = parser.extract(document_bytes, schema, rules=[stock_check])
```

Return `None` (or an empty list) when there's no issue. Any exception a rule raises is caught and logged (not silently swallowed). It won't crash extraction, but check your logs if a rule you expected to fire isn't showing up.

Rules run against the fully merged result (after multi-chunk merging), so they can safely compare fields that might live in different chunks of a long document (e.g. a total on page 1 vs. line items on page 8).
