"""Prompt compiler for generating dynamic Chain-of-Thought prompts from schemas."""

import json
from typing import Any

from .schema import Field, Schema


def _generate_json_structure(fields: list[Field]) -> dict[str, Any]:
    """Generate a sample JSON structure representing the schema."""
    structure: dict[str, Any] = {}
    for f in fields:
        if f.type == "list" and f.sub_fields:
            structure[f.name] = [_generate_json_structure(f.sub_fields)]
        else:
            structure[f.name] = f"<{f.type}> or null"
    return structure

def compile_prompt(schema: Schema) -> str:
    """Compile a deterministic CoT prompt from a Pydantic schema."""
    
    # Generate reasoning steps
    reasoning_steps = []
    for f in schema.fields:
        if f.type == "list" and f.sub_fields:
            sub_names = ", ".join([sub.name for sub in f.sub_fields])
            reasoning_steps.append(f"• What are the items for '{f.name}'? Identify {sub_names}.")
        else:
            reasoning_steps.append(f"• What is the '{f.name}'? {f.description}")

    # Generate extraction guide
    extraction_guide = []
    idx = 1
    for f in schema.fields:
        if f.type == "list" and f.sub_fields:
            sub_desc = ", ".join([f"{sub.name}: {sub.description}" for sub in f.sub_fields])
            extraction_guide.append(f"{idx}. {f.name}: List of items containing ({sub_desc}).")
        else:
            extraction_guide.append(f"{idx}. {f.name}: {f.description}")
        idx += 1
        
    json_structure = _generate_json_structure(schema.fields)
    json_str = json.dumps(json_structure, indent=2)

    examples_text = ""
    json_step_num = 3
    if schema.examples:
        examples_text = "\n═══════════════════════════════════════════════\nSTEP 3 — EXAMPLES\n═══════════════════════════════════════════════\n"
        for i, (doc_snippet, expected_json) in enumerate(schema.examples, 1):
            examples_text += f"Example {i}:\nDocument Snippet:\n{doc_snippet}\n\nExpected Output:\n```json\n{json.dumps(expected_json, indent=2)}\n```\n\n"
        json_step_num = 4


    prompt = f"""You are an expert document analyst.

Below is text extracted directly from a document (via layout-aware text parsing or OCR).
Analyze the document step by step and extract the requested fields for schema: {schema.name}.

═══════════════════════════════════════════════
STEP 1 — DOCUMENT REASONING (think out loud)
═══════════════════════════════════════════════
{chr(10).join(reasoning_steps)}

═══════════════════════════════════════════════
STEP 2 — FIELD EXTRACTION GUIDE
═══════════════════════════════════════════════
{chr(10).join(extraction_guide)}

{examples_text}═══════════════════════════════════════════════
STEP {json_step_num} — JSON OUTPUT
═══════════════════════════════════════════════
Output ONLY a valid JSON object in this exact structure:

```json
{json_str}
```

RULES:
- For addresses or multiline text: NEVER leave null if text is visible in the document. Combine all lines into a single string.
- Numbers only for numeric fields. No symbols/commas.
- If a field is missing, set to null.

══════════════════════
DOCUMENT TEXT:
══════════════════════
{{document_text}}
"""
    return prompt
