# fastdocparse

[![PyPI](https://img.shields.io/pypi/v/fastdocparse.svg)](https://pypi.org/project/fastdocparse/)
[![CI](https://github.com/pranjalparmar/fastdocparse/actions/workflows/ci.yml/badge.svg)](https://github.com/pranjalparmar/fastdocparse/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Extract structured data from semi-structured documents (invoices, bills, tax forms, resumes, bank statements, shipment manifests) using any OpenAI-compatible LLM (OpenAI, Ollama, vLLM, Groq, etc.), with **per-field grounding and confidence**, not just raw extraction.

## Why this, not just another parser

Most extractors give you a value and no way to know if it's real. This one tells you:

- **`grounded`**: the value was found verbatim (or near-verbatim) in the source document text.
- **`ungrounded`**: the value doesn't appear in the source, likely a hallucination. Flag for human review.
- **`missing_required`**: a field you marked required came back empty.
- **`invalid_format`**: the value doesn't match a pattern/enum constraint you declared (e.g. a shipment status outside the allowed list).
- **`failed_check`**: a custom cross-field rule failed (e.g. line items don't sum to the stated total).

No extra LLM call for any of this: it's deterministic, string/rule-based validation against text you already extracted.

**Where it fits:** semi-structured documents with recurring fields (invoices, bills, tax forms, resumes, statements), and prose documents where *proving* a value came from the source matters (contracts, legal clauses, insurance claims). It is not a vision-LLM pipeline. It works from extracted text (digital PDF text layer, or local OCR for scans/images), which is what keeps it fast, cheap, and usable with small local models. Messy handwritten forms or complex multi-column layouts are a known weaker spot (see [document-extractor-spec.md](document-extractor-spec.md)).

## How this compares

| Project | Approach | Where it beats fastdocparse | Where fastdocparse can beat it |
|---|---|---|---|
| **[Sparrow](https://github.com/katanaml/sparrow)** (katanaml) | Vision-LLM first (MLX/vLLM/Ollama/Mistral OCR), multi-service platform, API-first | Layout-aware (sees the page), mature, table templates for complex tables | No GPU required, single `pip install`, Pydantic-schema devex vs. raw JSON-string CLI args |
| **[LangExtract](https://github.com/google/langextract)** (Google) | Text-first, source-grounding (character-offset mapping), few-shot examples required | Real grounding/traceability, brand trust | Purpose-built for documents (OCR routing) vs. text-in/text-out |

**The honest edge:** *fast, cheap, local-model-friendly extraction for clean-to-moderate documents*, not "better than Sparrow at everything." This has not yet been validated with a real head-to-head benchmark ([tracking issue](https://github.com/pranjalparmar/fastdocparse/issues/19)). Treat it as a design goal, not a proven claim, until that lands.

## Two ways to use it

| | Who it's for | How |
|---|---|---|
| **CLI** | No coding needed | `fastdocparse extract <file> <schema.json>` |
| **Python API** | Building it into your own app | `DocumentParser(client).extract(document_bytes, schema)` |

Defining *what* to extract also has two paths: hand-write a JSON/YAML schema file, or describe it in plain English and let the LLM draft the schema for you.

## Install

```bash
pip install fastdocparse
```

For local development instead:

```bash
git clone https://github.com/pranjalparmar/fastdocparse
cd fastdocparse
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -e ".[dev]"
```

You also need access to an LLM. Either:
- An OpenAI API key (`export OPENAI_API_KEY=...` or pass `--api-key`), or
- A local model via [Ollama](https://ollama.com/): no API key, no cloud, documents never leave your machine.

## Quickstart: CLI (no coding)

```bash
# 1. Extract using one of the bundled example schemas
fastdocparse extract sample_invoice.png src/fastdocparse/schemas/invoice.json \
  --model gpt-4o-mini --api-key sk-...

# Or with a local model via Ollama (no API key needed):
fastdocparse extract sample_invoice.png src/fastdocparse/schemas/invoice.json \
  --model llama3.2 --base-url http://localhost:11434/v1 --api-key ollama
```

Output is JSON, printed to stdout (or saved with `--output result.json`):

```json
{
  "_meta": { "truncated": false, "truncation_reason": null },
  "invoice_number": { "value": "INV-9011", "confidence": "high", "flags": ["grounded"] },
  "total_price": { "value": 100.0, "confidence": "high", "flags": ["grounded"] }
}
```

Don't want to write JSON at all? Describe the fields in plain English instead:

```bash
fastdocparse schema-from-text \
  "I want the invoice number, total price, and vendor name. Invoice number and total are required." \
  --output my_invoice_schema.json

# review my_invoice_schema.json, then:
fastdocparse extract my_invoice.pdf my_invoice_schema.json
```

## Quickstart: Python API

```python
from fastdocparse import Schema, Field, LLMClient, DocumentParser

schema = Schema(
    name="Invoice",
    fields=[
        Field(name="invoice_number", description="The invoice number", required=True),
        Field(name="total_price", description="Total amount due", type="number", required=True),
    ],
)

client = LLMClient(model="gpt-4o-mini", api_key="sk-...")
# or: LLMClient(base_url="http://localhost:11434/v1", api_key="ollama", model="llama3.2")

parser = DocumentParser(client=client)

with open("invoice.pdf", "rb") as f:
    result = parser.extract(f.read(), schema)

print(result["invoice_number"])  # {'value': 'INV-9011', 'confidence': 'high', 'flags': ['grounded']}
```

## Full documentation

- [Getting Started](docs/getting-started.md): step-by-step install, CLI, and API walkthroughs
- [Schema Guide](docs/schema-guide.md): every field option (`type`, `required`, `pattern`, `enum`, `sub_fields`, few-shot `examples`), for JSON, YAML, and plain-English authoring
- [Output & Validation](docs/output-format.md): the full result shape, what each confidence flag means, and how to write custom cross-check rules
- [Architecture](docs/architecture.md): diagrams of the pipeline, the module dependency graph, and where to plug in a contribution
- [Project spec](document-extractor-spec.md): architecture, phased roadmap, honest competitive positioning

Want to contribute? Start with [docs/architecture.md](docs/architecture.md) for the map, then [CONTRIBUTING.md](CONTRIBUTING.md) for the process.

## Status

Core extraction, grounding, chunking, both CLI/API paths, and real packaging are implemented and tested (75 tests, `pytest -v`). Published on PyPI as [`fastdocparse`](https://pypi.org/project/fastdocparse/). `pip install fastdocparse` installs a working `fastdocparse` command and a proper `fastdocparse.*` import namespace, verified end to end with a clean-virtualenv install straight from the real public index. Not yet done: a hosted API. See [document-extractor-spec.md](document-extractor-spec.md) for the roadmap.
