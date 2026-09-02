# How fastdocparse Was Built

This is the story of how fastdocparse went from a single hardcoded invoice extractor to a general-purpose, schema-driven document extraction library. It's written for anyone curious about the "why" behind the current design, not as a spec to check boxes against (see [ROADMAP.md](../ROADMAP.md) for what's still ahead).

## Where it started

fastdocparse began as [`invoice-details-extractor`](https://github.com/pranjalparmar/invoice-details-extractor), a script hardcoded for one job: pull a fixed set of fields out of invoices, using one hand-tuned prompt and one hardcoded model client. It worked, but only for invoices, only with one model, and its "confidence" score was really just "did we get a non-empty string back," not a real signal.

The idea behind fastdocparse was to generalize that into something schema-driven and model-agnostic: describe what you want extracted, point it at any OpenAI-compatible LLM (OpenAI itself, a local Ollama model, vLLM, Groq, whatever), and get back structured JSON with an honest confidence signal per field.

## Phase 1: Making the schema and model runtime parameters

The first step was ripping out the hardcoded pieces. Instead of one fixed set of fields, `Schema` and `Field` (Pydantic models) let you describe any fields you want, with names, types, and descriptions. Instead of one hardcoded model client, `LLMClient` became a thin wrapper over any OpenAI-compatible `base_url`/`api_key`/`model`, so swapping providers is a parameter change, not a code change.

`DocumentParser.extract()` became the single orchestration point: route the document to the right ingestion path (digital PDF text via PyMuPDF, or OCR for scanned/image input), compile a prompt from the schema, call the LLM, parse and repair the JSON response. The existing JSON-repair logic (handling `<think>` tags, fenced code blocks, brace-matching for truncated output) carried over largely unchanged, it already worked, it just needed to serve arbitrary schemas instead of one.

## Phase 2: Grounding, so "confidence" means something

A confidence score that's just "field is non-empty" doesn't catch an LLM confidently making something up. Phase 2 added `grounding.py`: every non-null extracted value gets checked against the actual source text extracted from the document. If it's found (fuzzy substring match, with numeric- and date-aware comparison), it's flagged `grounded`. If not, it's flagged as a possible hallucination, without pretending everything is fine.

On top of that, `cross_check()` added pluggable sanity rules, the two built in are `numeric_sum_rule` (do line items actually sum to the stated total?) and `date_parseable_rule` (is this actually a valid date?). Both run as plain string/number comparisons, no extra LLM call, so grounding doesn't cost anything beyond the one extraction call.

## Phase 3: Few-shot examples, without hand-tuning the core prompt

Generalizing the prompt away from invoice-specific wording risked losing the accuracy that hand-tuning had bought. The fix was letting a `Schema` carry optional `examples`, pairs of a document snippet and its correct extraction, which get spliced into the compiled prompt when present. A schema with zero examples still works (falls back to the plain schema-only prompt); a schema with a couple of good examples can recover per-document-type accuracy without a special-cased prompt template living in the core library.

## Phase 4: Not throwing away the page layout

Flattening a page straight to plain text loses column and table structure, which breaks badly on multi-column layouts and real tables. `pdf_utils.py` gained a `structured_mode` that preserves bounding-box-derived layout as an intermediate markdown-table representation (`extract_layout_markdown_from_pdf`) instead of a flat text dump, used automatically when a schema has list/table-shaped fields. Non-table schemas are unaffected, they still take the plain flat-text path with no added overhead.

## Phase 5: Multi-page documents and chunking

The original extractor had a hardcoded 3-page cap. `pdf_utils.chunk_document_text()` splits longer documents into token-budgeted chunks, each gets its own extraction pass, and `parser._merge_extracted_data()` combines the results: list fields (like transactions) concatenate across chunks, and for scalar fields that show up in more than one chunk, the merge prefers whichever chunk's answer is actually grounded in that chunk's own source text, rather than blindly trusting whichever chunk ran first. A single-chunk document (the common case) is unaffected by any of this.

## Phase 6: Packaging and publishing

The library got a real `pyproject.toml`, a `cli.py` (`fastdocparse extract`, `fastdocparse schema-from-text`, `fastdocparse list-schemas`), and went out to PyPI. The original name, `docextract`, was rejected by PyPI as too similar to a pre-existing `doc-extract` package (PyPI treats `-`/`_`/case as equivalent for uniqueness), so everything, package name, import path, CLI command, and the GitHub repo itself, was renamed to `fastdocparse` rather than living with a mismatched PyPI/import name.

The one piece of Phase 6 not done yet is the head-to-head benchmark against comparable tools (Sparrow, LangExtract) on a shared test set. That's tracked in [ROADMAP.md](../ROADMAP.md), not claimed here.

## What's next

See [ROADMAP.md](../ROADMAP.md) for what's actively being considered: a real accuracy/latency benchmark, a hosted API, and a hybrid OCR+VLM escalation path for fields that come back ungrounded.
