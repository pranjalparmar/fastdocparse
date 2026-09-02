"""Compile a plain-English description of what to extract into a Schema, using the LLM.

Lets a non-technical user describe fields in their own words instead of writing JSON:

    "I want the bill of lading number (starts with BL-), the container number,
     the shipment status which is one of in_transit, delivered, or customs_hold,
     and the destination country. Bill of lading and destination are required."

The LLM proposes field names/types/constraints; the caller should review the
result (or the saved schema file) before relying on it for real extraction —
a hallucinated schema silently corrupts every later run, so this is meant as
an authoring aid with a human in the loop, not a fully implicit step.
"""

from typing import Any

from .json_repair import parse_json_from_llm
from .llm_client import LLMClient
from .schema import Schema

SCHEMA_GEN_PROMPT = """You are a data schema designer. A user described, in plain language, what \
information they want extracted from a document. Convert their description into a JSON schema.

Output ONLY a valid JSON object with this exact shape:
{{
  "name": "<short PascalCase schema name>",
  "fields": [
    {{
      "name": "<snake_case field name>",
      "description": "<clear description of what to extract, written for a document-extraction assistant to follow>",
      "type": "text" | "number" | "date" | "currency" | "list",
      "required": true | false,
      "pattern": "<regex the value must fully match, or null if the user didn't mention a specific format>",
      "enum": ["<allowed value>", "..."] or null,
      "sub_fields": [ {{ "name": "...", "description": "..." }} ]
    }}
  ]
}}

Rules:
- Use "list" + "sub_fields" only for repeating/tabular data (e.g. line items, containers). Every other field omits "sub_fields" (null).
- Set "required": true only if the description implies the field is essential/mandatory.
- Set "pattern" only when the description mentions a specific code/ID format (e.g. "starts with BL-", "6 digits"). Otherwise null.
- Set "enum" only when the description lists a fixed set of allowed values. Otherwise null.
- Field names must be snake_case, no spaces, no punctuation.
- Do not invent fields the user didn't ask for.

User's description:
\"\"\"
{description}
\"\"\"

Output ONLY the JSON object, nothing else."""


def compile_schema_from_description(description: str, client: LLMClient) -> Schema:
    """Turn a natural-language description into a Schema via one LLM call."""
    prompt = SCHEMA_GEN_PROMPT.format(description=description)
    raw_response = client.complete(prompt)
    data: dict[str, Any] = parse_json_from_llm(raw_response)

    if not data or not data.get("fields"):
        raise ValueError(
            "Could not turn that description into a schema. Try listing the specific fields you want, "
            "e.g. 'invoice number, total price, and a list of line items with product name and quantity'."
        )

    return Schema.from_dict(data)
