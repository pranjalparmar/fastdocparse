# Dynamic Document Extractor: Project Specification

**Status:** Published on PyPI as `fastdocparse`: Phases 1–5 complete, Phase 6 (packaging/publish) done; see [README.md](README.md) for current state. The rest of this document is preserved as the original planning record; sections below describing earlier phases as "not started" are historical, not current status.
**Base name:** `fastdocparse`: see [Naming](#naming) for how this was decided (`docextract`, the original top pick, was blocked by PyPI's project-name-similarity rule against the pre-existing `doc-extract`)
**Origin:** Generalization of `invoice-details-extractor` (github.com/pranjalparmar/invoice-details-extractor) into a schema-driven, model-agnostic document extraction library.

---

## 1. Idea, in one paragraph

A Python library that takes a PDF, PNG, or JPG, a user-defined schema describing what to extract, and a pointer to any CoT-capable LLM (OpenAI, Ollama, vLLM, any OpenAI-compatible endpoint), and returns validated structured JSON. It separates fast local document ingestion (digital-PDF text extraction or local OCR) from a single reasoning pass, avoiding the latency and GPU cost of vision-LLM-first pipelines, while remaining honest about the accuracy trade-offs that separation creates.

## 2. Why this, and the honest competitive position

| Project | Stars/backing | Approach | Where it beats us | Where we can beat it |
|---|---|---|---|---|
| **Sparrow** (katanaml) | 5.2k stars, 518 forks, commercial backing | Vision-LLM first (MLX/vLLM/Ollama/Mistral OCR), multi-service platform, API-first | Layout-aware (sees the page), mature, table templates for complex tables | No GPU required, single `pip install`, Pydantic-schema devex vs. raw JSON-string CLI args |
| **LangExtract** (Google) | Google-backed, press coverage | Text-first, source-grounding (character-offset mapping), few-shot examples required | Real grounding/traceability, brand trust | Purpose-built for documents (OCR routing) vs. text-in/text-out |
| ocrcontext, indoxminer, DELM, others | Small/low adoption | Various | n/a | Little competitive pressure from these |

**The honest edge:** *fast, cheap, local-model-friendly extraction for clean-to-moderate documents*, not "better than Sparrow at everything." This must be validated with real benchmarks before it's claimed publicly (see Phase 6).

**Known weaknesses in the current (invoice-only) approach that this spec exists to fix, in priority order:**
1. No grounding: a non-empty field is treated as "confidence: 95," which is not a real confidence signal.
2. Layout is discarded: bounding boxes get flattened into linear text before the LLM sees them, which breaks on multi-column layouts and complex tables.
3. No self-correction: one LLM call, one parse attempt, no cross-checks (e.g. line items vs. stated total).
4. Prompt quality is currently hand-tuned per document type (invoices); generalizing to arbitrary schemas risks losing that tuning unless few-shot examples are supported.
5. No chunking: hardcoded `max_pages=3`, unsuitable for statements/contracts.

---

## 3. Architecture overview

```
Input (PDF/PNG/JPG)
      │
      ▼
┌─────────────────┐
│ Ingestion router │  digital PDF → PyMuPDF (text + layout)
│                  │  scanned/image → local OCR (RapidOCR)
└─────────────────┘
      │
      ▼
┌─────────────────┐
│ Schema compiler  │  Pydantic schema (+ optional few-shot examples)
│                  │  → deterministic CoT prompt (no extra LLM call)
└─────────────────┘
      │
      ▼
┌─────────────────┐
│ LLM client layer │  any OpenAI-compatible base_url/api_key
│                  │  (OpenAI, Ollama, vLLM, Groq, etc.)
└─────────────────┘
      │
      ▼
┌─────────────────┐
│ Parse + validate │  JSON extraction/repair (existing logic)
│                  │  + grounding checks + cross-field sanity checks
└─────────────────┘
      │
      ▼
Structured output (JSON) + per-field confidence/flags
```

Core modules (extending the existing repo structure):
- `pdf_utils.py`: unchanged, digital PDF text/layout extraction
- `ocr_engine.py`: unchanged, local OCR fallback
- `schema.py`: **new**: Pydantic-based schema definition, field descriptions, optional few-shot examples
- `prompt_compiler.py`: **new**: deterministic template that turns a schema (+ examples) into a CoT prompt
- `llm_client.py`: **new**: thin adapter over any OpenAI-compatible endpoint
- `parser.py`: **new**: orchestrates the pipeline, exposes `DocumentParser`
- `grounding.py`: **new** (Phase 2): substring/fuzzy match of extracted values against source text, cross-field checks
- `cli.py`: **new** (Phase 6): thin CLI wrapper over the library

---

## 4. Phases

### Phase 1: Generalized core (workable prototype)

**Goal:** Same capability as the current invoice extractor, but schema and model are runtime parameters instead of hardcoded.

**Technical tasks:**
- [ ] Define `Schema` / `Field` classes (Pydantic-based): `name`, `type`, `description`, `required`
- [ ] Build `prompt_compiler.compile(schema) -> str`: deterministic template, no LLM call
- [ ] Build `llm_client.LLMClient(base_url, api_key, model)`: OpenAI-compatible wrapper; must work against at least OpenAI, a local Ollama instance, and one other OpenAI-compatible endpoint
- [ ] Build `DocumentParser.extract(document, schema, model) -> dict` orchestrating: route → compile prompt → call LLM → parse/repair (reuse existing `_parse_json_from_llm` logic)
- [ ] Preserve existing routing logic from `pdf_utils.py` / `ocr_engine.py` unchanged
- [ ] Remove hardcoded `FIELD_SCHEMA` / `FAST_COT_PROMPT` / `GLM_TEXT_MODEL` / `get_fhgenie_client()` dependencies from the core path (invoice-specific prompt can remain as a *pre-built example schema*, not the only path)

**Required system behavior:**
- Given a schema with N fields and a document, the system returns a dict with exactly those N keys (missing data → `null`, not omitted).
- Swapping `model=` between two different OpenAI-compatible endpoints must require no code change, only the parameter.
- A digital PDF must route through PyMuPDF; a PNG/JPG or scanned PDF must route through OCR. This must be automatic, not user-specified.
- If the LLM response isn't valid JSON on first parse, the existing repair fallback (strip `<think>` tags, extract fenced block, brace-match) must still recover it.

**Test cases:**
- [ ] TC1.1: Extract a known-good digital invoice PDF with the original invoice schema; output matches the current extractor's output field-for-field.
- [ ] TC1.2: Extract the same document with a *different*, arbitrary schema (e.g. 3 unrelated fields); output has exactly those 3 keys.
- [ ] TC1.3: Extract a scanned/image receipt (JPG) with a receipt-specific schema; confirm OCR path is used (assert via log/route flag) and output is non-empty.
- [ ] TC1.4: Point `model=` at two different endpoints (e.g. OpenAI vs. local Ollama model) for the same document/schema; both return valid JSON matching the schema shape (values may differ).
- [ ] TC1.5: Feed a deliberately malformed LLM response (simulate via a stub client that returns text with a `<think>` block and extra prose) through the parser; confirm correct JSON is still recovered.
- [ ] TC1.6: Feed a document missing a requested field; confirm the field comes back `null`, not omitted or hallucinated with a placeholder.
- [ ] TC1.7: Benchmark: run TC1.1 five times, log latency; confirm it's within the same order of magnitude as the original invoice extractor (no regression from the abstraction layers).

---

### Phase 2: Grounding & sanity checks

**Goal:** Replace the placeholder "confidence: 95 if non-empty" with a real, cheap signal: no extra LLM call.

**Technical tasks:**
- [ ] Build `grounding.check_substring(value, source_text) -> bool`: fuzzy match extracted value against the raw OCR/PyMuPDF text already available
- [ ] Build `grounding.cross_check(schema, extracted) -> list[Issue]`: pluggable rule hooks (e.g. "line items sum to total", "date is parseable")
- [ ] Attach a `confidence` and `flags: list[str]` per field to the output, derived from the above (not a fixed constant)
- [ ] Document the confidence semantics clearly (e.g. "grounded" = found in source text; "ungrounded" = not found, possible hallucination; "failed_check" = cross-check rule failed)

**Required system behavior:**
- Every extracted non-null field must be checked against the source text; if not found (even fuzzily), it must be flagged, not silently marked high-confidence.
- If a schema declares a numeric list field plus a total field, and they don't sum correctly, both must be flagged.
- Grounding checks must not require any additional model call (deterministic/string-based only).

**Test cases:**
- [ ] TC2.1: Extract a field whose value appears verbatim in the source text; confirm it's marked grounded/high-confidence.
- [ ] TC2.2: Use a stub LLM client that returns a plausible but fabricated value not present in the source text; confirm it's flagged as ungrounded.
- [ ] TC2.3: Extract line items that sum to a different total than the stated total field; confirm both are flagged by the cross-check.
- [ ] TC2.4: Extract a date field with an unparseable value; confirm it's flagged.
- [ ] TC2.5: Confirm Phase 2 adds no additional LLM API calls (mock/count calls in a test harness; count must equal Phase 1's count).

---

### Phase 3: Few-shot schema definition

**Goal:** Recover per-document-type accuracy without hand-tuning the core prompt template.

**Technical tasks:**
- [ ] Extend `Schema` to accept optional `examples: list[tuple[str, dict]]` (document snippet → correct extraction)
- [ ] Extend `prompt_compiler.compile()` to splice examples into the prompt when present
- [ ] Provide at least one pre-built example schema (invoice) with real few-shot examples, migrated from the current hand-tuned prompt

**Required system behavior:**
- A schema with 0 examples must still work (falls back to Phase 1 behavior).
- A schema with 1–3 examples must produce measurably better accuracy than 0 examples on a held-out test document of the same type (see test cases).

**Test cases:**
- [ ] TC3.1: Run the same non-invoice schema (e.g. a receipt schema) with 0 examples vs. 2 examples against a held-out document; log accuracy (field-level exact match against a manually labeled ground truth) for both; confirm the with-examples run is equal or better.
- [ ] TC3.2: Confirm the invoice schema, rebuilt with few-shot examples instead of the old hand-written CoT prompt, matches or beats the original hand-tuned extractor's accuracy on the same test set (this determines whether the generalization "worked").
- [ ] TC3.3: Confirm a schema with malformed/mismatched examples (example dict doesn't match schema fields) raises a clear validation error at schema-definition time, not silently at extraction time.

---

### Phase 4: Layout-awareness for tables

**Goal:** Stop discarding layout before the LLM sees the document; specifically fix multi-column and table extraction.

**Technical tasks:**
- [ ] Preserve bounding-box/column metadata through ingestion instead of flattening immediately to plain text
- [ ] Add an opt-in "structured" ingestion mode for schemas containing list/table-type fields, which passes layout-annotated text (or an intermediate HTML/markdown table representation) to the LLM instead of a flat dump
- [ ] Evaluate whether a lightweight table-reconstruction step (e.g. row/column clustering from bounding boxes) is needed before this is reliable, or whether prompt-level hints suffice

**Required system behavior:**
- A schema with a `list[dict]` (table-like) field must, in structured mode, produce row-consistent output on a genuinely multi-column source document, not just single-column-friendly ones.
- Non-table schemas must be unaffected (no regression to Phase 1–3 behavior/latency for simple field extraction).

**Test cases:**
- [ ] TC4.1: Extract a table field from a known multi-column layout (e.g. a two-column bank statement or bonds table); compare row/column fidelity against flat-text mode from Phase 1 on the same document.
- [ ] TC4.2: Confirm a non-table schema run in Phase 4 code has no measurable latency regression vs. Phase 1.
- [ ] TC4.3: Stress test: a document with a genuinely complex table (merged cells, multi-row headers); document known failure modes even if not fully solved: this phase's exit criterion is "measurably better than flat-text, honestly documented gaps," not perfection.

---

### Phase 5: Multi-page & chunking

**Goal:** Remove the hardcoded page cap; support longer documents.

**Technical tasks:**
- [ ] Remove `max_pages=3` hardcoding
- [ ] Add chunking strategy for documents beyond a configurable page/token threshold
- [ ] Add a merge step for schema fields that may span chunks (e.g. transactions across pages of a statement)

**Required system behavior:**
- A 10+ page document must not fail or silently truncate; either process in full via chunking or return a clear, explicit "truncated" flag with the reason.
- Schema fields that are lists (e.g. transactions) must accumulate correctly across chunks, not just return the last chunk's data.

**Test cases:**
- [ ] TC5.1: Extract a list-type field from a synthetic 10-page document where relevant rows are spread across pages 1, 5, and 9; confirm all rows are present in the merged output.
- [ ] TC5.2: Extract a single-value field from page 8 of a 10-page document; confirm it's found (not missed due to chunk boundary).
- [ ] TC5.3: Feed a document exceeding any configured hard limit; confirm the system returns a clear truncation flag rather than silently dropping data.

---

### Phase 6: Benchmark, package, publish

**Goal:** Prove the speed/cost claim, ship it properly.

**Technical tasks:**
- [ ] Finalize package name (see [Naming](#naming)); register on PyPI
- [ ] Build `pyproject.toml`, package structure, `cli.py` as a thin wrapper over the library
- [ ] Run a head-to-head benchmark against Sparrow's `sparrow-parse` pipeline on a shared test set (10–20 mixed documents: invoices, receipts, forms): log latency, cost (if applicable), and field-level accuracy for both
- [ ] Write README leading with the comparison table and honest scope ("fast/local option for clean-to-moderate documents," not "better than Sparrow at everything")
- [ ] Publish to PyPI; prepare launch posts for r/LocalLLaMA, r/Python, Show HN

**Required system behavior:**
- `pip install <package>` then a 3-line script must successfully extract a schema from a sample document with zero additional setup beyond an API key or local Ollama install.

**Test cases:**
- [ ] TC6.1: Fresh virtual environment, `pip install` from PyPI (not local dev install), run the README quickstart verbatim; confirm it works with no undocumented steps.
- [ ] TC6.2: Run the benchmark suite against both this library and Sparrow on the same 10–20 documents; confirm numbers are reproducible on a second run (within reasonable variance) before publishing them.
- [ ] TC6.3: CLI smoke test: `<cli> --file invoice.pdf --schema schema.json --model ollama/llama3` produces valid JSON output to stdout/file.

---

## 5. Naming

| Name | PyPI status (checked) |
|---|---|
| `fastparse` | ❌ Taken (unrelated Tree-sitter binding package) |
| `zerovlm` | ✅ Available |
| `docparse` | ❌ Taken |
| `docextract` | ✅ Available |
| `fastdocparse` | ✅ Available |
| `doc-fastparse` | ✅ Available |

Decision: `docextract` was the original pick and was fully built under that name (import path, CLI command, GitHub repo), but PyPI rejected the upload with "the name 'docextract' is too similar to an existing project" (it collides with the pre-existing `doc-extract`; PyPI treats `-`/`_`/case as equivalent for uniqueness, which a plain exact-name availability check doesn't catch). Everything was renamed to `fastdocparse`: package, import name, CLI command, and the GitHub repo itself, rather than keeping a PyPI-name/import-name mismatch, since consistency was worth more than avoiding the rename churn.

---

## 6. Open questions / risks to revisit before publishing claims

- [ ] Does removing hand-tuning (Phase 1 → Phase 3) cost more accuracy than few-shot examples recover? (Answered by TC3.2.)
- [ ] Is the CPU-only/no-GPU pipeline actually competitive on accuracy with Sparrow's vision-LLM approach on messy documents, or only on clean ones? (Answered by Phase 6 benchmark.)
- [ ] Is a lightweight table-reconstruction step (Phase 4) sufficient, or does hard table extraction eventually require a vision component too, undercutting the "no GPU needed" claim for that subset of documents? (Flag honestly in docs either way.)

---

## 7. Overall progress checklist

- [ ] **Phase 1**: Generalized core (7 test cases)
- [ ] **Phase 2**: Grounding & sanity checks (5 test cases)
- [ ] **Phase 3**: Few-shot schema definition (3 test cases)
- [ ] **Phase 4**: Layout-awareness for tables (3 test cases)
- [ ] **Phase 5**: Multi-page & chunking (3 test cases)
- [ ] **Phase 6**: Benchmark, package, publish (3 test cases)
