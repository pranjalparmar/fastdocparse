# Schema Guide

A schema declares *what* to extract. It works identically whether you write it as JSON, YAML, or Python: same fields, same meaning, same validation.

## Minimal schema

```json
{
  "name": "Invoice",
  "fields": [
    { "name": "invoice_number", "description": "The invoice number" }
  ]
}
```

`name` is a label for the schema (used in prompts and logs). `fields` is the list of things to extract.

## Field options

| Key | Type | Default | Meaning |
|---|---|---|---|
| `name` | string | n/a (required) | Output key. Use `snake_case`, no spaces. |
| `description` | string | n/a (required) | Tells the LLM what to look for. Be specific: this is the main lever for extraction accuracy. |
| `type` | `"text"` \| `"number"` \| `"date"` \| `"currency"` \| `"list"` | `"text"` | `"list"` triggers structured (layout-aware) extraction mode automatically. Other types are descriptive hints for the LLM, not enforced coercion. |
| `required` | bool | `false` | If `true` and the field comes back empty, the result is flagged `missing_required`. |
| `pattern` | string (regex) or `null` | `null` | If the extracted value doesn't fully match this regex, flagged `invalid_format`. Use for structured codes: HS codes, container numbers, SSNs, invoice number formats. |
| `enum` | list of strings or `null` | `null` | If the value isn't one of these, flagged `invalid_format`. Use for closed vocabularies: shipment status, currency codes, filing status. |
| `sub_fields` | list of `Field` or `null` | `null` | Only for `type: "list"`: describes each item's columns (e.g. a line-items table). |

## Example: a table field (`sub_fields`)

```json
{
  "name": "line_items",
  "description": "Every product row in the invoice.",
  "type": "list",
  "sub_fields": [
    { "name": "product_name", "description": "Name of the product" },
    { "name": "unit_price", "description": "Price per unit" },
    { "name": "quantity", "description": "Quantity" }
  ]
}
```

Result comes back as a list of dicts: `[{"product_name": "Widget A", "unit_price": 5.0, "quantity": 10}, ...]`.

## Example: constraints for a non-invoice domain

```json
{
  "name": "bill_of_lading",
  "description": "Bill of Lading (B/L) number identifying the shipment.",
  "required": true,
  "pattern": "BL-\\d{4,}"
}
```

```json
{
  "name": "shipment_status",
  "description": "Current status of the shipment.",
  "enum": ["in_transit", "delivered", "customs_hold", "delayed"]
}
```

See [`src/fastdocparse/schemas/shipment_manifest.json`](../src/fastdocparse/schemas/shipment_manifest.json) for the full example.

## Few-shot examples (optional, improves accuracy)

Schemas can include example (document snippet → expected output) pairs. Useful when a field is ambiguous or the document type has quirks a plain description doesn't capture.

```json
{
  "name": "Invoice",
  "fields": [
    { "name": "invoice_number", "description": "The invoice number" },
    { "name": "total_price", "description": "Grand total", "type": "number" }
  ],
  "examples": [
    [
      "Invoice: INV-9011\nGrand Total: USD 100.00",
      { "invoice_number": "INV-9011", "total_price": 100.0 }
    ]
  ]
}
```

Each example is a 2-element array: `[document_snippet_string, expected_output_object]`. The expected output must cover the fields you want to demonstrate; it doesn't need every field in the schema.

See [`src/fastdocparse/schemas/invoice.json`](../src/fastdocparse/schemas/invoice.json) for a complete example with a few-shot pair.

## Loading a schema file

**Python:**
```python
from fastdocparse import Schema
schema = Schema.from_file("src/fastdocparse/schemas/invoice.json")   # or .yaml/.yml
```

**CLI:** pass the path directly: `fastdocparse extract doc.pdf src/fastdocparse/schemas/invoice.json`.

## Writing a schema in Python instead of JSON

Same shape, as Pydantic objects:

```python
from fastdocparse import Schema, Field

schema = Schema(
    name="ShipmentManifest",
    fields=[
        Field(name="bill_of_lading", description="B/L number", required=True, pattern=r"BL-\d{4,}"),
        Field(name="shipment_status", description="Status", enum=["in_transit", "delivered", "customs_hold"]),
    ],
)
```

Use this path when you need something a static schema file can't express, e.g. building the field list dynamically at runtime.

## Generating a schema from plain English

If you don't want to write JSON or Python at all:

```bash
fastdocparse schema-from-text \
  "I want the bill of lading number (starts with BL-), shipment status which is one of in_transit, delivered, or customs_hold, and the destination country. Bill of lading and destination are required." \
  --output schemas/my_manifest.json
```

This sends your description to the LLM once and writes a schema file in the exact format above. **Always open and review the generated file before trusting it for real extraction.** The LLM is inferring field names, types, and constraints from your wording, and mistakes here are silent (a wrong `pattern` or missing `required` won't error, it'll just quietly under- or over-flag every document you later run against this schema).

Good descriptions to feed it:
- List the specific fields you want, don't just name the document type ("extract the fields" is too vague; "extract invoice number, total price, and vendor name" works).
- Mention formats explicitly if you want a `pattern` ("container number, which is 4 letters followed by 7 digits").
- Mention closed value sets explicitly if you want an `enum` ("status is one of pending, shipped, or delivered").
- Say which fields are essential if you want `required: true` ("invoice number and total are required").
