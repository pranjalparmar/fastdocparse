# MinerU and Marker internals: parsing pipelines, structures, and trade-offs

**Research snapshot:** 12 November 2026
**Code baselines:** MinerU `4.2.1` (release tag `mineru-4.2.1-released`, commit `a8c0cf815ce97433989c9c74f185674551f0fd4c`, released 11 November 2026); Marker `2.1.0` (tag `v2.1.0`, released 29 October 2026).
**Scope:** PDF/image input through native text extraction, rendering/OCR, layout, reading order, formula/table/image handling, postprocessing, output schemas, model/runtime selection, deployment, and licensing. This is a source-and-documentation architecture review, not an independent quality evaluation.

## Executive summary

MinerU 4.x and Marker 2.x are both staged document parsers, but their center of gravity differs. **MinerU 4.2.1 exposes explicit quality tiers and routes PDF analysis through a hybrid, windowed pipeline:** native PDF text is available in the Flash/text path; learned layout/OCR/formula/table components are added at higher effort levels; Standard and Advanced use a VLM. A shared `ModelJson` → `MiddleJson` document contract then supports renderers, structured content, asset packaging, SDK/API and document-library reading. MinerU is also a wider document-processing product, covering many native office/web formats and local service/library workflows. [1–5]

**Marker 2.1.0 is organized around providers, builders, a mutable block-tree `Document`, processors and renderers.** For PDFs, it extracts positioned native text with `pdftext`, renders pages for layout and visual fallback, and chooses selectively between PDF text, OCR, and repair. Its `fast` mode favors lightweight layout plus native text and targeted repairs; `balanced` uses more VLM inference, including full-page OCR on pages judged unreliable. Equations get their own OCR processor. Digital tables are reconstructed heuristically from PDF text; scanned or low-confidence tables fall back to recognition. [6–12]

The shared architectural lesson is to keep **native and visual evidence, geometry, semantic structure, and output serialization distinct**. Neither Markdown nor a headline benchmark score describes the complete internal result or a guarantee for an arbitrary corpus. Benchmark claims below are attributed to their publishers and should be reproduced on the target data and hardware. Model and component licenses must be checked separately from repository code.

## 1. Version and evidence boundaries

The release page identifies MinerU 4.2.1 as published on 11 November 2026. Its release fixes include a native-PDF text-direction edge case and document-library continuation/recovery behavior; those are useful release-specific caveats, not a substitute for the full architecture trace. Marker 2.1.0 was published on 29 October 2026 and upgrades its Surya dependency to `surya-ocr>=0.23.0,<0.24.0`; the release says Surya 2.5 is the default and documents an environment-variable override to remain on Surya 2. [1, 6]

**Evidence hierarchy:** release-pinned source and package metadata for implementation and dependency details; official documentation/README for supported contracts and documented defaults; technical papers for motivation and historical system descriptions. The 2024 MinerU technical report describes an earlier generation and should not be read as the MinerU 4.2.1 runtime spec. Marker README performance tables are the project’s own benchmark results, not a neutral comparative study. [13–15]

## 2. End-to-end architecture at a glance

```text
MinerU 4.2.1
path/bytes -> MinerUParser -> input normalization / format routing
  -> PDFDocument + (tier-dependent) native text, rendered-page layout,
     OCR, formula/table recognition, optional VLM/image analysis
  -> ModelJson -> shared deterministic postprocess -> MiddleJson
  -> Markdown / HTML / LaTeX / DOCX / EPUB / PDF / structured content / content list
  -> ParseResult, CLI/API packaging, asset references, or Doclib reading

Marker 2.1.0 (PDF)
path/bytes -> PdfConverter -> PdfProvider (PDFium + pdftext text/geometry)
  -> DocumentBuilder (low-res render, layout, native-line quality/order,
     selective high-res render, OCR where needed)
  -> StructureBuilder -> ordered processor list
  -> renderer (Markdown / HTML / JSON / chunks; images/assets alongside)
```

These diagrams show the conceptual sequence. MinerU uses page windows and resource-scoped model execution; Marker also has batched model calls and a multi-worker/shared-inference-server deployment pattern. Neither should be assumed to process every page in a simple, synchronous one-page-at-a-time loop. [3, 7, 9, 10]

## 3. MinerU 4.2.1

### 3.1 Input routes, tiers, and model selection

The public Python `parse()` entrypoint constructs `MinerUParser`; the parser resolves file type, reads bytes, normalizes image input through a PDF representation, validates page-range applicability, and calls the shared `backend.analyze.doc_analyze()` entrypoint. That analysis facade dispatches PDF, CSV/TSV, EPUB, HTML/MHTML, OFD, and Office-family inputs to format-specific analyzers, then constructs `ModelJson` and the shared postprocessed `MiddleJson`. PDF page ranges are supported; non-PDF native document parsing is whole-document. [2–4]

For PDFs and images, the documented tiers map to analysis effort as follows:

| Public tier | Analysis effort | Practical interpretation |
|---|---|---|
| `flash` | `flash` | Native PDF text/document parsing can avoid inference in text mode. Flash OCR for scans/images is a separate model-backed route; do not equate all Flash requests with “no models.” |
| `basic` | `medium` | Small-model pipeline; OCR, formula and table handling without a local VLM requirement. |
| `standard` | `high` | Small models plus VLM-assisted extraction/layout for more complex pages. |
| `advanced` | `xhigh` | Standard runtime with greater inference effort and optional image analysis enabled by the parser interface. |

`mineru-kit parse` and local Python `parse()` document Standard as their PDF default. Native formats such as Office, EPUB, HTML/MHTML, OFD and CSV/TSV use local Flash parsing. Small-model backend and VLM engine are configured independently (`onnx`/`torch`; `llama.cpp`/`vLLM`/`LMDeploy`/`MLX` where supported); they are not aliases for quality tiers. The supported engine/device matrix depends on OS, installed extras, drivers and hardware. The docs give 16 GB system/unified memory as a planning recommendation for higher-throughput workloads, not a guarantee that every PDF or concurrency level fits. [4, 5]

### 3.2 PDF evidence, rendering and OCR

The pinned PDF analysis pipeline creates a `PDFDocument`, resolves `parse_mode=auto` to text or OCR, and has a special Flash + text route that calls DocVortex’s native PDF analyzer. That route returns native model blocks and attaches visual assets only when visual blocks need them; it does not require the hybrid inference context. Other paths acquire the small-model runtime and use a shared PDF-window pipeline (default window size 64, configurable through `MINERU_PROCESSING_WINDOW_SIZE`). Pages are rendered in a window, passed through the small layout model, and then filled with text/formula/table evidence according to effort and parse mode. [3, 9]

The `txt` path preserves PDF text geometry and can prioritize native table recovery. In higher-effort paths, layout proposals are converted into VLM-style blocks; High and Xhigh invoke VLM extraction, while OCR and formula components add or repair content. At Medium, the system performs dedicated local formula and table recognition and fills block text from PDF/OCR evidence. OCR can also provide scan text and specialized seal text. Windowed processing, explicit cleanup and memory trimming are part of the implementation rather than incidental deployment details. [3, 9]

**Operational consequence:** “native text available” is not the same as “native text is good.” A PDF may have no usable text layer, a corrupted/duplicated layer, or correct words with misleading ordering/geometry. Explicit `ocr_mode`/`parse_mode` selection changes the evidence route. Force-OCR or model-tier changes should be deliberate because they affect cost, runtime, and potentially the text evidence itself. [3–5]

### 3.3 Layout and reading order

MinerU’s PDF flow combines page layout proposals and text/visual evidence, then normalizes model output and assembles document-level blocks. In the pinned `window.py`, the small layout model is run for non-Flash-text PDF windows. Higher-effort routes use VLM results; Xhigh additionally normalizes its VLM blocks and applies title splitting. The resulting analysis structure retains per-page blocks and geometry in the model protocol. [3, 9]

Do not reduce this to “layout detector equals reading order.” Region detection, text extraction, block ownership, ordering, and cross-page merging are separate inference/postprocessing concerns. Complex columns, sidebars, floating figures, footnotes, and page-spanning tables remain difficult. MinerU 4.2 release material emphasizes source navigation and merged multi-region visualization in the UI; that improves inspection but does not make every inferred order or relation correct. [1, 3, 9]

### 3.4 Tables, formulas, and images

Table treatment is tier-sensitive and deliberately hybrid. MinerU can recover digital table structure from native PDF geometry/text when it is suitable; the Medium route contains a dedicated table-classification/recognition flow, while High can preserve accepted native table results and send remaining blocks to the VLM. Complex native candidates can be left on the model fallback path. OCR-mode table content can be projected from recognized text. These branches mean it is inaccurate to characterize every table as either “native” or “VLM generated.” [9, 11]

Formula handling also has multiple routes. Layout provides formula regions; Medium can run formula recognition and separately OCR formula numbers; High/Xhigh TXT paths use inline recognition where appropriate and avoid re-running successful slices. Formula-number blocks can be merged with adjacent equations. Inference failures or mismatched geometry can still leave empty, misplaced or degraded formula content. [9, 12]

Image and other visual-block payloads are associated with blocks and serialized as assets. Output packaging updates references across Markdown, Middle JSON and Structured Content; current documentation warns that a standalone structural JSON may not contain image bytes, while saved output packages include assets. Consumers should resolve image references from the result package rather than infer filenames. [2]

### 3.5 Postprocessing, schemas, and renderers

MinerU has a meaningful separation between raw analysis and consumer representation:

- **`ModelJson`** is the analysis/model-output container, including page blocks and page-index mapping. It is internal and is not included in ordinary SDK, CLI, Tasks or Gradio results.
- **`MiddleJson`** is the shared, postprocessed document model. The current documented serialized protocol identifies itself with `schema: "docvortex.middle"` and `schema_version: "2.0"`; it contains metadata, extensions, pages and typed blocks. The MinerU extension records effective tier and parse mode; PDF layout geometry is carried in a DocVortex extension.
- **Structured Content** is a consumer-friendly projection, not another name for the intermediate schema. `ParseResult` and the renderer package expose several views and asset-aware save behavior.

The renderer supports nine targets: Markdown, HTML, LaTeX, DOCX, EPUB, PDF, Structured Content, Content List V1 and Content List V2. Availability differs by entrypoint: for example, `mineru-kit parse` documents Markdown, Middle JSON and ZIP, while SDK rendering and service endpoints expose their own subsets. A renderer’s existence does not imply every CLI/API accepts that format. [2]

This typed, versioned contract is an architectural strength: geometry and blocks survive beyond Markdown rendering. It also means integrations must respect schema identity/version, source page numbering (zero-based internally versus one-based CLI ranges), asset packaging, and historical-result compatibility. The migration docs explicitly distinguish readable cached legacy results from automatically rewritten data; re-parsing may be necessary to restore missing assets or geometry. [2, 5]

### 3.6 Deployment and failure modes

MinerU 4.x is more than an importable parser: it includes a local document library, cache/continuation operations, V1 API/Tasks, CLI, web UI, model download/verification, and routing/service operations. Remote parsing is not implicit in the local parser’s documented flow; remote services must be configured/selected. For sensitive documents, distinguish local model inference from any explicitly configured remote VLM/API endpoint. [4, 5]

Useful failure cases to test include: bad or rotated native text; scan/hybrid pages; OCR language/quality issues; layout boxes that miss or overlap; table geometry that cannot be recovered; formula crops and number association; large documents stressing memory; unavailable backend wheels/drivers/models; model-server startup/teardown errors; and output packages with missing/unresolved assets. MinerU 4.2.1 specifically improves a native PDF text-angle edge case and bounded managed-server recovery, signaling that implementation/runtime failures matter alongside recognition quality. [1, 3–5]

**License:** MinerU 4.2.1’s repository contains `LICENSE.md`, describing Apache 2.0 plus additional conditions: commercial use is allowed without a separate license below consolidated affiliate thresholds of 100 million monthly active users and USD 20 million total monthly revenue; online services to third parties must clearly and prominently attribute MinerU; breach can terminate rights. Review this exact version’s full license and all model/dependency/data licenses before shipping—do not carry forward historical AGPL/CC-BY-NC claims from older releases or infer that the code license settles every bundled model’s terms. [16]

## 4. Marker 2.1.0

### 4.1 Input, providers, and runtime modes

Marker’s `PdfConverter` chooses a provider from the input path and composes `DocumentBuilder`, `LayoutBuilder`, `LineBuilder`, `OcrBuilder`, `StructureBuilder`, processors and a renderer. This architecture makes providers, builders, processors and renderers visible extension seams. The pinned package defaults to a Markdown renderer and a standard processor sequence; LLM-backed processors are present but opt-in via `use_llm`/service configuration, rather than a requirement for every conversion. [7, 8, 10]

For PDFs, `PdfProvider` uses PDFium for document access and rendering and `pdftext` to extract positioned lines, spans, character positions, font properties, URLs/references and page geometry. It can cache character-level page data to support table reconstruction, with higher memory use. It checks for usable text (including empty/garbled text and conditions around existing OCR) and can force OCR or strip existing OCR through configuration. [10]

`DocumentBuilder` renders low-resolution pages at 96 DPI for layout. It renders high-resolution pages at 192 DPI only when pages or downstream blocks need them (for example OCR, tables, equations), with a lazy-loader fallback. `PdfConverter` selects `balanced` on CUDA and `fast` on CPU/MPS when mode is not explicitly set. `balanced` uses the Surya layout/VLM path and full-page OCR for pages judged bad; `fast` uses a lightweight RF-DETR/ONNX layout detector, keeps PDF text where trustworthy, and limits recognition to problematic pages/blocks and math. These are defaults, not exclusive capabilities: callers can configure the mode and OCR behavior. [7–9]

### 4.2 Native text acceptance, OCR, and reading order

`LineBuilder` decides page by page whether embedded PDF text is trustworthy. It evaluates a text-quality signal, layout coverage, line overlap/geometry and the presence of provider lines. Good pages receive `pdftext` lines; pages deemed bad are marked for Surya recognition. On otherwise good pages, it can flag empty or garbled blocks for targeted OCR; in `fast` mode, a sufficiently high fraction of flagged blocks can promote the whole page to OCR. `balanced` is more conservative about keeping a page once any block is suspect. OCR can run full-page (the default) to retain context or blockwise; full-page OCR failure can fall back to existing layout blocks. [8, 9]

On pages with usable PDF text, Marker can order blocks by the PDF character stream’s `minimum_position` rather than by top-left geometry or Surya’s order head. Layout model order remains relevant for textless/scanned pages. This is an explicit hybrid policy: embedded text order is treated as a useful signal but not blindly accepted when the page is classified as bad. A broken or unusual content stream can still mislead it. [8]

The `--disable_ocr` path removes VLM calls, including equation OCR, but is not equivalent to full visual document understanding: scanned text is skipped and equations may have no recoverable textual form. `--force_ocr` intentionally discards the native-text advantage on all pages; use it for suspicious text layers, not as an assumed universal quality improvement. [7, 9]

### 4.3 Layout, structure, formulas, and tables

`LayoutBuilder` obtains regions from Surya’s layout predictor in balanced mode or the fast detector in fast/no-OCR modes, scales their polygons into page coordinates, maps known labels to block types, and skips unknown labels with a warning. Some figure/picture/complex-region boxes may be expanded into nearby whitespace. Layout output proposes regions; it does not by itself provide final text, a semantic hierarchy, or guaranteed reading order. [8]

`StructureBuilder` groups caption/footnote blocks near tables, figures and pictures, recognizes list item patterns and groups nearby items. The configured processor chain then performs block-level work such as line merge, list/header/footnote/equation handling, table processing, optional LLM enrichment, and cleanup. Processors mutate a shared `Document`; renderers serialize that result. [7, 11]

Equations are a dedicated case. `EquationProcessor` recognizes equation and chemical blocks not already populated by page OCR, and can use math-like glyph/font hints to OCR inline-math text blocks in balanced mode (auto setting). The recognition output is cleaned into block HTML/MathML-like markup and the Markdown renderer turns math into configured delimiters. If OCR is disabled, the processor leaves native text-layer content rather than fabricating a visual transcription. [12]

There is **no dedicated table-structure neural model in Marker 2.1.0’s default `TableProcessor`**. Digital tables use deterministic CPU heuristics over cached `pdftext` lines/characters: tokenize rows, test span/projection-based grid candidates, score grid regularity/type/fill, infer header bands, and emit HTML. If the PDF is scanned or the reconstruction is unavailable/low-confidence, Marker sends a high-resolution table crop to Surya for HTML recognition. The HTML may carry row/column spans, but the heuristic path is not a guaranteed structural recovery algorithm; sparse, borderless, nested, merged, rotated or irregular tables can remain difficult. Optional LLM table processing/merge is a separate enhancement. [11]

Images are extracted and associated with block references for HTML/Markdown and JSON output. Optional LLM image-description processing can add derived descriptions. In JSON, page/block polygons and image payload references preserve more than Markdown; chunks flatten top-level blocks with full HTML per block. The exact result depends on renderer/output format and extraction settings. [7]

### 4.4 Document representation and rendering

Marker’s internal `Document` is a Pydantic model holding pages; page/block objects have identifiers, types, polygons, child/structure references, and HTML/content fields. Pages retain a structure sequence over the block collection; groups and contained blocks express relationships such as lists, table/figure captions and nested content. JSON exports a page/block tree with polygons, children, HTML and images; Markdown is a renderer projection; chunks flatten top-level blocks. [7, 13]

The Markdown renderer first constructs HTML from the document, then converts HTML into Markdown. It translates `<math>` into inline/display delimiters and can render tables as Markdown grids or preserve HTML tables. In its default Markdown-table conversion it has to approximate span structures in a rectangular grid, and values like pipes/newlines are normalized. Thus JSON/HTML are generally safer when cell-level spans or richer block metadata matter. [13]

Marker’s output model is useful and mutable for integrations but does not have MinerU’s explicitly documented shared `ModelJson`/`MiddleJson` protocol boundary. The developer-facing extension philosophy is instead to add providers for input formats, processors for transformations, and renderers for output formats. [7, 13]

### 4.5 Deployment, benchmark claims, and known limitations

Marker’s README describes Surya inference served locally: vLLM on supported NVIDIA setups and llama.cpp for CPU/Apple Silicon, with an optional remote inference URL. Multi-file conversion uses workers sharing an inference server, with concurrency budgeting. Long-document memory pressure, server capacity, and reducing worker count are practical operational considerations. Model/runtime downloads and optional document-format dependencies add install footprint. [7]

Marker 2.1.0’s README reports its own `olmocr-bench` results over 1,403 PDFs (~8,400 pass/fail tests): 76.0% overall for balanced, 66.6% for fast, and 43.6% for fast/no-OCR; born-digital-only results differ. The README describes a macro-average over benchmark categories and reports throughput under concurrent execution on a B200 host. These are **publisher-reported** numbers for its pinned benchmark setup, not independent guarantees or directly comparable figures unless the competitor versions, modes, hardware, data, concurrency, and scoring are matched. The project itself notes weaker cases such as very complex nested layouts/tables and forms. [7]

For Marker code version 2.1.0, `pyproject.toml` declares Apache-2.0. This does not mean the model weights use Apache: the repository’s `MODEL_LICENSE` is a modified AI Pubs OpenRAIL-M license. It includes use restrictions, distribution/attribution/share-alike conditions and commercial limits (the README summarizes research/personal use and startups below USD 5M funding/revenue; the full license also includes a competitor restriction). Confirm eligibility and obligations in the full license and the specific Surya checkpoint/model cards. Optional hosted/API services have distinct terms and data handling. [6, 14]

## 5. Direct comparison

| Concern | MinerU 4.2.1 | Marker 2.1.0 |
|---|---|---|
| Core design | Explicit tiers, shared analysis facade, `ModelJson` and `MiddleJson`, render/package/document-library layers. | Provider → builders → mutable block document → processors → renderer. |
| Native PDF evidence | Flash/TXT can use native analysis without inference; higher routes combine native geometry with model paths and table recovery. | `pdftext` lines/spans/characters plus PDFium geometry/rendering; page/block quality checks decide whether to keep or replace text. |
| Visual/layout path | Small-model layout in hybrid PDF path; High/Xhigh VLM extraction; CPU/GPU/model engine choice configurable. | Fast RF-DETR/ONNX layout or balanced Surya layout/VLM, with page-specific OCR and high-resolution crops. |
| Reading order | A product-level block pipeline with geometry and postprocessing; avoid interpreting detector order alone as truth. | Uses PDF character-stream positions on trusted text pages; OCR/order model for scanned pages; can fail with anomalous streams/layouts. |
| Tables | Native table recovery plus learned/classified table recognition depending on tier and suitability; OCR text projection on scan paths. | Heuristic reconstruction from digital PDF text; crop-based Surya fallback; no dedicated table-structure model in default processor. |
| Math | Formula detector/recognizer, effort-dependent MFR/VLM paths, formula-number OCR and association. | Equation and optional inline-math blocks processed with Surya; pure no-OCR path cannot visually recover equations. |
| Canonical output | Explicit versioned shared middle schema plus structured and asset-aware exports. | Block-tree JSON/document plus renderer-specific Markdown/HTML/chunks; relationships and assets depend on chosen output. |
| Format/product breadth | PDF/images plus broad native office/web formats, SDK, API, Doclib and UI. | PDF-centric conversion with image input and optional extras for office/web/ebook formats; processor/renderer extensibility. |
| Default policy | Python/`mineru-kit` PDF default is Standard; Flash and explicit tier selection available. | balanced on CUDA; fast on CPU/MPS; both configurable. |
| License review | Apache 2.0 with MinerU-specific commercial threshold and online-service attribution clauses; also review model/dependency licenses. | Apache code, modified OpenRAIL-M model weights with restrictions; review exact weights and third-party components. |

These systems should not be ranked from feature lists alone. A benchmark must match document families and parse modes, compare like execution tiers, account for hardware and concurrency, evaluate content and structure separately, and include installation, maintenance, and license constraints.

## 6. Design lessons for `fastdocparse`

### Worth borrowing

- Preserve native text and geometry alongside rendered-page evidence; route to OCR/layout only where the text layer or task requires it.
- Keep region detection, text acquisition, reading order, structure assembly, and rendering as separate stages with inspectable diagnostics.
- Represent tables as grids/cells and formulas/images as typed blocks or asset references in the canonical representation; render Markdown only at the edge.
- Record evidence provenance (PDF text, OCR, model, heuristic, postprocess), page coordinates and model/configuration versions so disagreements are auditable.
- Make resource-intensive inference optional and explicit. A clean digital invoice should not silently trigger a large VLM deployment.
- Add page/block overlays and quality warnings for missing text, questionable order, low-coverage layout, empty table reconstruction, and unresolved visual assets.
- Test with a document-disjoint corpus containing scans, hybrid PDFs, multi-column pages, tables, formulas, figures, non-Latin scripts, rotated text, forms and business documents; include CPU and target accelerator modes.

### Avoid copying wholesale

- Do not adopt either project’s whole model stack, runtime server, broad format matrix or schema solely because it exists. The dependency, startup, model-download, memory and licensing costs are substantial.
- Do not treat a single parser’s block taxonomy or Markdown output as the public canonical schema. Use a small versioned IR and adapters.
- Do not assume layout labels imply semantic truth, OCR confidence is calibrated, or table HTML is lossless.
- Do not use vendor benchmarks as a substitute for a local, fixed-version evaluation with the project’s real target mix.

## 7. Open evaluation questions

1. On the repository’s target document set, how do MinerU Flash/TXT, Basic, Standard and Advanced compare with Marker fast, balanced and no-OCR for field-level accuracy, tables, math, order, latency, memory and warm/cold startup?
2. What fraction of our PDFs have clean native text, usable native order and sufficiently accurate geometry? How often does selective OCR outperform full-page OCR?
3. Which output format retains enough provenance and table structure for downstream extraction without forcing users to adopt either upstream schema?
4. What is the total installation/model footprint and operational burden on supported CPU/MPS/NVIDIA environments?
5. Which model weights and transitive dependencies are acceptable under the project’s intended commercial, hosted-service and redistribution scenarios?

## References

### MinerU

1. OpenDataLab, [MinerU 4.2.1 release](https://github.com/opendatalab/MinerU/releases/tag/mineru-4.2.1-released), 11 Nov. 2026; [pinned source tree](https://github.com/opendatalab/MinerU/tree/mineru-4.2.1-released).
2. OpenDataLab, [Output Formats and Result Contract](https://opendatalab.github.io/MinerU/reference/output_files/), current 4.x documentation.
3. OpenDataLab, MinerU 4.2.1 source: [`mineru/parser/mineru_parser.py`](https://github.com/opendatalab/MinerU/blob/mineru-4.2.1-released/mineru/parser/mineru_parser.py), [`mineru/backend/analyze.py`](https://github.com/opendatalab/MinerU/blob/mineru-4.2.1-released/mineru/backend/analyze.py), [`mineru/backend/analysis/pdf/pipeline.py`](https://github.com/opendatalab/MinerU/blob/mineru-4.2.1-released/mineru/backend/analysis/pdf/pipeline.py), [`mineru/backend/analysis/pdf/window.py`](https://github.com/opendatalab/MinerU/blob/mineru-4.2.1-released/mineru/backend/analysis/pdf/window.py), and [`mineru/backend/postprocess/document.py`](https://github.com/opendatalab/MinerU/blob/mineru-4.2.1-released/mineru/backend/postprocess/document.py).
4. OpenDataLab, [Tiers and Runtimes](https://opendatalab.github.io/MinerU/usage/tiers/), current 4.x documentation.
5. OpenDataLab, [Migration to 4.0](https://opendatalab.github.io/MinerU/reference/migration_4/) and [Migration to 4.1](https://opendatalab.github.io/MinerU/reference/migration_4_1/).
9. OpenDataLab, MinerU 4.2.1 PDF modules: [`tables.py`](https://github.com/opendatalab/MinerU/blob/mineru-4.2.1-released/mineru/backend/analysis/pdf/tables.py), [`formulas.py`](https://github.com/opendatalab/MinerU/blob/mineru-4.2.1-released/mineru/backend/analysis/pdf/formulas.py), and [`ocr.py`](https://github.com/opendatalab/MinerU/blob/mineru-4.2.1-released/mineru/backend/analysis/pdf/ocr.py).
16. OpenDataLab, [MinerU Open Source License (`LICENSE.md` at 4.2.1)](https://github.com/opendatalab/MinerU/blob/mineru-4.2.1-released/LICENSE.md).
17. B. Wang et al., “MinerU: An Open-Source Solution for Precise Document Content Extraction,” arXiv:2409.18839, 2024, [paper](https://arxiv.org/abs/2409.18839). Historical architecture/claims; not the 4.2.1 implementation contract.

### Marker

6. Datalab, [Marker 2.1.0 release](https://github.com/datalab-to/marker/releases/tag/v2.1.0), 29 Oct. 2026; [pinned README](https://github.com/datalab-to/marker/blob/v2.1.0/README.md), [`pyproject.toml`](https://github.com/datalab-to/marker/blob/v2.1.0/pyproject.toml), and [`MODEL_LICENSE`](https://github.com/datalab-to/marker/blob/v2.1.0/MODEL_LICENSE).
7. Datalab, Marker 2.1.0 README, [source-pinned copy](https://raw.githubusercontent.com/datalab-to/marker/v2.1.0/README.md) (usage, deployment, reported benchmark, limitations).
8. Datalab, Marker 2.1.0 source: [`marker/builders/line.py`](https://github.com/datalab-to/marker/blob/v2.1.0/marker/builders/line.py), [`marker/builders/layout.py`](https://github.com/datalab-to/marker/blob/v2.1.0/marker/builders/layout.py), and [`marker/builders/ocr.py`](https://github.com/datalab-to/marker/blob/v2.1.0/marker/builders/ocr.py).
10. Datalab, Marker 2.1.0 source: [`marker/converters/pdf.py`](https://github.com/datalab-to/marker/blob/v2.1.0/marker/converters/pdf.py), [`marker/builders/document.py`](https://github.com/datalab-to/marker/blob/v2.1.0/marker/builders/document.py), and [`marker/providers/pdf.py`](https://github.com/datalab-to/marker/blob/v2.1.0/marker/providers/pdf.py).
11. Datalab, Marker 2.1.0 source: [`marker/processors/table.py`](https://github.com/datalab-to/marker/blob/v2.1.0/marker/processors/table.py) and [`marker/processors/table_recon.py`](https://github.com/datalab-to/marker/blob/v2.1.0/marker/processors/table_recon.py).
12. Datalab, Marker 2.1.0 [`marker/processors/equation.py`](https://github.com/datalab-to/marker/blob/v2.1.0/marker/processors/equation.py).
13. Datalab, Marker 2.1.0 [`marker/schema/document.py`](https://github.com/datalab-to/marker/blob/v2.1.0/marker/schema/document.py), [`marker/builders/structure.py`](https://github.com/datalab-to/marker/blob/v2.1.0/marker/builders/structure.py), and [`marker/renderers/markdown.py`](https://github.com/datalab-to/marker/blob/v2.1.0/marker/renderers/markdown.py).
14. Datalab, [Marker model license](https://github.com/datalab-to/marker/blob/v2.1.0/MODEL_LICENSE), modified AI Pubs OpenRAIL-M, version 0.1 (modified 2023).

### Comparative benchmark context

15. Datalab, [Marker 2.1.0 README benchmark section](https://raw.githubusercontent.com/datalab-to/marker/v2.1.0/README.md#benchmarks), project-reported `olmocr-bench` results. The benchmark dataset and configuration are described by the README; reproduce using the linked benchmark harness before relying on ranking or throughput claims.
