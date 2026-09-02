# Getting Started

Step-by-step for both the no-code (CLI) path and the developer (Python API) path.

## 1. Install

```bash
git clone https://github.com/pranjalparmar/fastdocparse
cd fastdocparse
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -e .
```

## 2. Get access to an LLM

You need one of:

- **OpenAI**: an API key from https://platform.openai.com/. No local install.
- **A local model via Ollama** (recommended if your documents are sensitive, since nothing leaves your machine):
  1. Install Ollama: https://ollama.com/
  2. Pull a model: `ollama run llama3.2` (or any model that supports JSON-mode-style output)
  3. Ollama serves an OpenAI-compatible API at `http://localhost:11434/v1` automatically.
- **Any other OpenAI-compatible endpoint** (vLLM, Groq, etc.): just its base URL, API key, and model name.

Nothing in this project is tied to OpenAI specifically. Swapping `--base-url`/`--model` is the only change needed to switch providers.

---

## Path A: CLI (no coding)

### A.1 Pick or write a schema

A schema is a JSON (or YAML) file listing the fields you want extracted. Two are bundled as starting points:

- [`src/fastdocparse/schemas/invoice.json`](../src/fastdocparse/schemas/invoice.json): invoice number, dates, parties, line items, few-shot examples included.
- [`src/fastdocparse/schemas/shipment_manifest.json`](../src/fastdocparse/schemas/shipment_manifest.json): bill of lading, container number, HS code, shipment status (with `required`/`pattern`/`enum` constraints).

Copy one and edit field names/descriptions for your document type, or write your own from scratch. See the [Schema Guide](schema-guide.md) for every option.

Not sure what's bundled or where it lives? List them from the CLI:

```bash
fastdocparse list-schemas
```

**Don't want to write JSON at all?** Describe what you want in plain English:

```bash
fastdocparse schema-from-text \
  "Extract the invoice number, total price, and vendor name. Invoice number and total are required." \
  --output schemas/my_invoice.json
```

This makes one LLM call, writes `schemas/my_invoice.json`, and prints a confirmation. **Open the file and check it before using it for real extraction.** The LLM is guessing field names/types from your wording, and a wrong guess here will affect every document you run against this schema afterward.

### A.2 Run extraction

```bash
fastdocparse extract <document.pdf-or-png-or-jpg> <schema.json> \
  --model gpt-4o-mini \
  --api-key sk-...
```

For a local Ollama model instead:

```bash
fastdocparse extract document.pdf src/fastdocparse/schemas/invoice.json \
  --model llama3.2 \
  --base-url http://localhost:11434/v1 \
  --api-key ollama
```

Options:

| Flag | Meaning | Default |
|---|---|---|
| `--model`, `-m` | Model name (`gpt-4o-mini`, `llama3.2`, ...). Also settable via `FASTDOCPARSE_MODEL`. | `gpt-4o-mini` |
| `--base-url` | OpenAI-compatible endpoint URL. Omit for real OpenAI. Also settable via `FASTDOCPARSE_BASE_URL`. | OpenAI |
| `--api-key` | API key. Also settable via `LLM_API_KEY` or `OPENAI_API_KEY`. Any string works for local Ollama. | none |
| `--output`, `-o` | Save the JSON result to a file instead of printing it | stdout |

PDF vs. image is detected automatically from the file extension.

**Repeating `--model`/`--base-url`/`--api-key` on every command gets old fast if you're always using the same local model.** Set them once as environment variables instead:

```bash
export FASTDOCPARSE_MODEL=llama3.2
export FASTDOCPARSE_BASE_URL=http://localhost:11434/v1
export LLM_API_KEY=ollama

fastdocparse extract document.pdf src/fastdocparse/schemas/invoice.json   # no flags needed
```

An explicit flag always overrides the matching env var, so you can still override per-command when needed. If nothing is configured at all (no flag, no env var, no `--base-url`), the CLI fails fast with setup instructions instead of trying to reach OpenAI and getting an auth error.

### A.3 Read the result

See [Output & Validation](output-format.md) for the full shape and what each flag means.

---

## Path B: Python API

### B.1 Define a schema in code

```python
from fastdocparse import Schema, Field

schema = Schema(
    name="Invoice",
    fields=[
        Field(name="invoice_number", description="The invoice number", required=True),
        Field(name="total_price", description="Total amount due", type="number", required=True),
        Field(name="vendor_name", description="Name of the company issuing the invoice"),
    ],
)
```

Or load the same JSON/YAML file the CLI uses:

```python
from fastdocparse import Schema
schema = Schema.from_file("src/fastdocparse/schemas/invoice.json")
```

### B.2 Create an LLM client

```python
from fastdocparse import LLMClient

client = LLMClient(model="gpt-4o-mini", api_key="sk-...")
# or, for local Ollama:
client = LLMClient(base_url="http://localhost:11434/v1", api_key="ollama", model="llama3.2")
```

### B.3 Extract

```python
from fastdocparse import DocumentParser

parser = DocumentParser(client=client)

with open("invoice.pdf", "rb") as f:
    document_bytes = f.read()

result = parser.extract(document_bytes, schema)
```

For an image (PNG/JPG) instead of a PDF, pass `is_image=True`:

```python
result = parser.extract(image_bytes, schema, is_image=True)
```

### B.4 Add custom validation rules (optional)

```python
from fastdocparse import numeric_sum_rule, date_parseable_rule

result = parser.extract(
    document_bytes,
    schema,
    rules=[
        numeric_sum_rule(list_field="line_items", total_field="total_price", item_key="unit_price"),
        date_parseable_rule("invoice_date"),
    ],
)
```

See [Output & Validation](output-format.md) for how to write your own rules from scratch.

### B.5 Tune extraction behavior (optional)

```python
from fastdocparse import ExtractionConfig

config = ExtractionConfig(max_pages=10, chunk_max_tokens=4000)
parser = DocumentParser(client=client, config=config)
```

### B.6 Add support for a new document format (optional)

Only PDF and images (PNG/JPG) are built in. To add another format (DOCX, XLSX, ...),
write a function that turns document bytes into text and register it:

```python
from fastdocparse import DocumentParser

def extract_docx_text(document_bytes: bytes, structured_mode: bool, config) -> str:
    ...  # your DOCX-to-text logic
    return text

parser = DocumentParser(client=client)
parser.register_ingestion_handler("docx", extract_docx_text)   # scoped to this parser only

result = parser.extract(docx_bytes, schema, kind="docx")
```

`DocumentParser.register_ingestion_handler()` (instance method) scopes the handler to
that one parser. There's also a module-level `parser.register_default_ingestion_handler()`
that changes what *new* `DocumentParser()` instances get by default. It's deliberately a
different name, so it's never confused with the instance-scoped version at a call site.

**Using this from the CLI:** a fresh CLI process only knows the built-in `pdf`/`image`
handlers, so a custom `kind` needs to be registered before the CLI dispatches. Set
`FASTDOCPARSE_PLUGINS` to a comma-separated list of importable module names. Each one is
imported at CLI startup, so put your `register_default_ingestion_handler(...)` call at
module level in that file:

```bash
FASTDOCPARSE_PLUGINS=my_project.docx_plugin fastdocparse extract contract.docx schema.json --kind docx
```

**Security note:** this imports and runs whatever Python is in the module(s) you name,
with no sandboxing, the same trust model as `PYTHONSTARTUP` or `DJANGO_SETTINGS_MODULE`.
That's fine for pointing it at your own plugin on your own machine. Never let
`FASTDOCPARSE_PLUGINS` be set from an untrusted source (e.g. a parameter in a hosted
service built on top of this CLI).

---

## Running the tests

```bash
pip install -e ".[dev]"
pytest -v
```
