# Multimodal OCR and document-structure extraction systems

**Research date:** November 12, 2026
**Scope:** Mistral OCR (including OCR 3 and the documented current OCR 4.1), Marker, olmOCR, Nougat, and Docling. This note distinguishes text recognition, layout/structure analysis, serialization, and downstream semantic extraction. Vendor claims and benchmark results are identified as such; recommendations for `fastdocparse` are inferences, not measured findings.

## Executive summary

These systems do not solve an identical problem. Mistral OCR is a hosted OCR/document-processing API that returns Markdown and optional structured page/block data. Marker, olmOCR, Nougat, and Docling are open-source or source-available pipelines with different dependencies and target domains: Marker combines document extraction and vision/OCR components; olmOCR uses an end-to-end vision-language model; Nougat maps scientific-page images to scholarly markup; Docling combines PDF-native evidence with layout/table models and typed document assembly. Their respective outputs therefore cannot be ranked on a single “OCR quality” number without specifying the task and evaluation protocol. [S1–S10]

The practical distinction is **recognition vs. reconstruction**. Recognizing text does not, by itself, establish correct reading order, table topology, heading hierarchy, key-value relationships, or a lossless representation. A model may return clean Markdown while dropping coordinates, visual assets, cell spans, ambiguity, or source provenance. Conversely, a structured JSON result can preserve relationships and geometry without guaranteeing that the recognized words or inferred relations are correct. [S2, S3, S6, S8, S9]

As of this review, Mistral documents **OCR 4.1** (`mistral-ocr-4-1`, dated July 16, 2026) as its latest OCR model. Its listed rate is $4 per 1,000 pages, or $5 per 1,000 pages with annotations; OCR 3 (`mistral-ocr-2512`) remains listed at $2 per 1,000 pages. These are documented API prices, not total cost-of-ownership comparisons. Mistral’s claims about benchmark leadership are vendor-reported and should be treated as directional because the releases, model versions, datasets, runners, and scoring methods are not uniform across sources. [S1, S3, S4, S5]

For `fastdocparse`, the strongest design implication is to preserve a canonical, evidence-grounded document representation and treat OCR, layout detection, table parsing, and semantic extraction as distinct replaceable stages. Keep each backend’s raw output and provenance; do not make Markdown the sole archival parse. Evaluate local and hosted alternatives on a document-disjoint target set before adopting a default. [Inference based on S2, S6–S10]

## 1. What the systems produce: separate capability layers

The labels below describe documented system behavior, not guarantees that every page will be parsed correctly.

| Layer | Main question | Examples of output | What it does **not** establish by itself |
|---|---|---|---|
| **OCR / text recognition** | What characters or words are visible? | Text, tokens, sometimes word boxes and confidence | Correct semantic role, reading sequence, table grid, or transcription accuracy on a particular document |
| **Layout analysis** | Where are page regions and what visual class might they have? | Paragraph, heading, table, image, header/footer regions; boxes or polygons | That region labels are semantically correct or that the page’s logical order is unambiguous |
| **Structure reconstruction** | How do regions relate? | Reading order, hierarchy, table rows/cells, key-value or caption links | That the inferred relationships are source-authored facts or fully recoverable from the image |
| **Serialization** | How is the result represented for consumers? | Markdown, HTML, JSON, XML/LaTeX or a typed in-memory document | That a readable projection is lossless or that a structured representation is correct |
| **Semantic extraction** | What task-specific fields should be returned? | Invoice number, dates, totals, entities or schema-shaped output | That field values are grounded unless source spans/regions and provenance are retained and checked |

Mistral’s OCR endpoint can return page Markdown and optional tables in HTML or Markdown, and its OCR 4+ documentation describes structured page/block outputs including paragraph-level bounding boxes and labels, reading order, dimensions, and confidence signals at page, block, and word levels. Annotation is a separate document-processing operation that adds a schema-oriented extraction step; it should not be conflated with OCR output or treated as proof that arbitrary structured extraction is inherent in the OCR model. [S2, S3]

Docling documents a typed `DoclingDocument` with text, tables, pictures, hierarchy, bounding boxes, and provenance, and supports multiple serializers. Marker and olmOCR/Nougat expose different intermediate and rendered forms; a plain-text or Markdown artifact is only the capabilities of that particular output projection, not necessarily the full internal evidence. [S6–S10]

## 2. Mistral OCR: OCR 3 and current API behavior

### OCR 3 release-era claims vs. current capabilities

Mistral announced OCR 3 in December 2025 as an OCR model intended for complex document types, with text and layout handling and a reported price of $2 per 1,000 pages. Those are claims from the OCR 3 announcement and its release context; they should not be silently carried forward as a description of the newest model or every current endpoint option. The current API model listing continues to identify `mistral-ocr-2512` as OCR 3. [S3, S4]

Mistral’s OCR 4 announcement describes an upgraded model, reports support for worldwide language coverage (the announcement states 170 languages), offers enterprise self-hosting, and publishes benchmark comparisons and pricing. Those are Mistral’s descriptions and results; the reviewed material does not disclose enough model architecture, training data, evaluation details, or independent reproduction to infer the internals or generalize the reported ranking to all document types. [S5]

The currently documented model page identifies **OCR 4.1** (`mistral-ocr-4-1`), dated July 16, 2026. Its listed rates are $4/1,000 pages and $5/1,000 pages with annotations; OCR 3 is listed at $2/1,000 pages. The pricing page should be checked at deployment time because API rates may change. [S1, S4]

### Documented output and workflow

- The OCR processor documentation describes per-page Markdown output and optional table formats, with images, tables, hyperlinks, and page metadata available through the documented processing flow. OCR 4+ structured blocks include paragraph-level labels and bounding boxes in reading order. [S2]
- The API reference documents the OCR endpoint and its request/response contract. Exact response options and fields should be checked against the selected model/API version rather than assumed from an announcement example. [S3]
- Confidence fields at page, block, and word level are signals supplied by the service, not a demonstrated universal probability of correctness. The reviewed sources do not establish common calibration against the other systems in this comparison. [S2]
- Document annotations are an additional step/call after OCR in the documented workflow. They can be used for schema extraction, but their behavior, additional cost, and failure modes are distinct from OCR transcription. [S2, S4]

### Trade-offs, deployment, and evidence gaps

**Strengths documented by the vendor:** managed API workflow; structured output options beyond a single text string; documented pricing; and an enterprise self-hosting option described in the OCR 4 announcement. [S1–S5]

**Constraints and risks:** Hosted processing puts the source document through a remote service unless an eligible self-hosted deployment is selected. The cited model/API pages do not establish a universal latency SLA, maximum end-to-end throughput for a user’s workload, or privacy/data-retention terms for every account configuration; those need to be confirmed in the applicable contract and service documentation. The reviewed sources also do not publish OCR model weights or sufficient architecture detail to characterize the model as a specific recognizer/VLM design. [S1–S5]

The API’s structured output is useful evidence, but it is not a losslessness guarantee. Markdown cannot by itself encode every geometric, hierarchy, ambiguity, asset, or table-span detail. Optional annotation output is a model interpretation layered on top of the OCR result. Preserve the original file, raw API response, model identifier, options, and any annotation response separately if auditability matters. [S2–S4; final sentence is an implementation inference]

## 3. Comparison systems

| System | Documented approach and intended use | Structure/serialization profile | Operational and licensing considerations |
|---|---|---|---|
| **Marker** | Open-source PDF/document conversion pipeline with native PDF text extraction where available, layout/OCR components, and optional LLM-based processing or repair. Repository feature set and behavior evolve; inspect the pinned release and configuration used. [S6] | Produces Markdown and supports structured output paths; some layout and image information can be retained, but Markdown remains a projection. Its hybrid path differs fundamentally from a single end-to-end image-to-text model. [S6] | Self-hosting avoids per-page API fees but incurs runtime, model, and hardware/dependency costs. Review the repository’s current license and any model/data licenses independently before redistribution or commercial deployment. The cited repository is not a latency guarantee. [S6] |
| **olmOCR / olmOCR 2** | olmOCR 2 is described in its paper as an end-to-end vision-language model trained using verifiable-reward reinforcement learning for document OCR, targeting high-quality PDF-to-text/markup conversion. [S7] | Its output format and benchmark objectives emphasize faithful text/markup conversion, but an end-to-end generated transcription is not automatically a typed, coordinate-preserving canonical document graph. The repository’s olmOCR-Bench uses unit tests that target structural/content properties beyond exact string matching. [S7, S10] | Open weights/code and self-hosting options can enable local processing, but actual cost depends on GPU, batch size, model version, and serving stack. Check the exact model and repository licenses and hardware guidance for the chosen release. Published benchmark results are not directly interchangeable with vendor OCR API results. [S7, S10] |
| **Nougat** | Vision-transformer OCR model for scientific documents: page images are converted into markup intended to preserve scholarly structure that can be lost in conventional PDF text extraction. Its paper documents repetitions on challenging inputs and limitations with non-English/multilingual documents. [S8] | Scholarly markup is useful for mathematical/scientific content, but it is not a general-purpose schema extractor or a universal coordinate-level layout representation. Its target domain should not be generalized to arbitrary invoices, receipts, forms, handwriting, or business PDFs. [S8] | Self-hostable research software/model; deployment cost and rights depend on the specific code and model artifact. Validate current licenses and maintenance status for the pinned version. Do not infer modern API service guarantees from the paper. [S8] |
| **Docling** | Modular document-conversion pipeline. Its technical report describes PDF text/position extraction plus rendered-page visual analysis, layout and table models, OCR options, post-processing, reading-order assembly, and a typed document model. [S9] | `DoclingDocument` can represent text, tables, pictures, hierarchy, boxes and provenance. Serializers include Markdown and HTML; format capabilities differ. For example, its serialization documentation explains that table spans preserved in the internal grid and suitable structured outputs are flattened by Markdown. [S9, S11] | Local/self-hostable with model and library dependencies; its report discusses speed/memory and CPU/GPU trade-offs for particular pipeline components, not a universal current throughput figure. The project is open source; check the pinned package, model assets, and current license notices for deployment. [S9, S12] |

### Architectural distinction

Marker and Docling are best understood as **pipelines** that can combine native PDF information with visual models and post-processing. Their behavior can vary by file type, available text layer, configuration, and optional components. This can help avoid re-recognizing clean PDF text, but creates integration complexity and possible disagreement among evidence sources. [S6, S9]

olmOCR 2 and Nougat are **image-to-markup generative approaches**. They can infer coherent text and structure from page imagery, but generated output may omit or normalize details and is not equivalent to retaining source tokens and geometry. Nougat’s stated scholarly focus is narrower than arbitrary document parsing; olmOCR’s published objective and benchmarks should likewise be interpreted within their evaluated tasks. [S7, S8, S10]

Mistral OCR is a **managed document-processing service** with documented page/block output and optional additional annotation. It offers a simpler API integration, but that convenience trades away local control unless the documented enterprise self-host option is used. [S1–S5]

None of these broad descriptions establishes that one system is best for every mix of born-digital PDFs, scans, multilingual pages, complex tables, forms, and visual documents. That conclusion would require an evaluation on the intended corpus. [Inference]

## 4. Accuracy, benchmarks, and known failure modes

### Benchmark interpretation

Mistral’s release posts publish vendor benchmark comparisons, while olmOCR and Docling papers/repos report results under their own tasks and evaluation setups. OmniDocBench has evolved through versions (the repository documents v1.6/v1.7-era results and evaluation changes), and its maintainers note output-format and matching issues. olmOCR-Bench includes unit tests intended to evaluate structural requirements that simple string metrics can miss. These differences matter: model release, preprocessing, page rendering, OCR configuration, prompting, output format, matching, dataset version, and hardware/runner can all change the result. [S5, S9, S10, S13]

Consequently:

1. Treat reported numbers as **directional within the source’s defined test**, not as a shared league table across providers and repositories. [S5, S7–S10, S13]
2. Do not compare exact-match/string-edit scores to table-structure, layout, or unit-test scores as if they measured one construct. A transcription may be textually close while its table topology is wrong, or structurally plausible while cell text is wrong. [S10, S13]
3. Keep vendor-reported results separate from independent evaluation. The reviewed sources do not provide a single independent, same-version, same-runner head-to-head covering all five systems on the same broad document set. [S5–S13]
4. Report per-document and per-category results, not just a pooled average; distinguish clean digital text, scans, tables, multi-column pages, mixed-language pages, handwriting, and scientific notation. [Inference]

### Failure modes to test

The following are risks implied by the task and the systems’ documented representations, not assertions that each system always exhibits every failure:

- **Recognition:** low-resolution/blurred scans, small fonts, skew, handwriting, faint text, unusual scripts, mathematical notation, and glyph confusion can produce substitutions, omissions, or hallucinated text. Nougat explicitly documents challenging-input repetition and multilingual limitations. [S8]
- **Reading order:** multi-column pages, sidebars, footnotes, floating figures, headers/footers, and cross-page continuations can be linearized incorrectly. OCR text in a plausible sequence is not proof of physical or logical reading order. [S2, S6, S9]
- **Tables:** merged cells, nested headers, blank cells, spanning labels, borderless tables, and tables crossing pages can be flattened or misaligned. A Markdown table may look readable while losing grid semantics or spans. [S2, S6, S9, S11]
- **Visual evidence:** charts, diagrams, stamps, signatures, and images can be omitted or represented only by a placeholder/caption in a text projection. A text conversion should not be assumed to capture all visual information. [S2, S6, S9, S11]
- **Semantic extraction:** a model can confuse visually similar values (e.g., subtotal and total), infer a key/value association incorrectly, or return schema-valid but unsupported values. Annotation/schema output needs field-level source evidence and validation. [S2; inference]
- **Confidence:** vendor confidence values should not be compared across providers or treated as calibrated error probabilities absent validation on the target data. [S2; inference]

## 5. Cost, latency, privacy, deployment, and license

| Dimension | Mistral OCR | Local/open pipelines and models |
|---|---|---|
| **Published cost** | OCR 4.1: $4/1,000 pages; $5/1,000 pages with annotations. OCR 3: $2/1,000 pages. Use current pricing documentation for final estimates. [S1, S4] | No equivalent per-page price from the cited projects. Cost comprises hardware/compute, engineering, model downloads/storage, serving, and maintenance; measure with the actual workload. [Inference] |
| **Latency / throughput** | Reviewed docs provide API behavior and pricing but no universal end-to-end latency guarantee for a customer workload. API/network, file preparation, pages per request, service load, and annotation stages add variability. [S1–S4] | Depends on CPU/GPU, model size, batching, PDF rendering, OCR/layout stack, and pipeline configuration. Published paper timings, if given, are configuration-specific and not directly predictive of current production throughput. [S6–S9] |
| **Deployment** | Managed API; OCR 4 announcement describes an enterprise self-hosting option. Verify current eligibility and operational terms. [S5] | Marker, olmOCR, Nougat, and Docling can be run locally to varying degrees; required dependencies, models, and accelerators differ. Follow the pinned release’s installation guidance. [S6–S9] |
| **Privacy / data control** | A hosted API means sending document data to the service. Applicable retention, region, training-use and contractual controls must be confirmed for the user’s plan. Enterprise self-hosting may change this architecture. [S5; account-specific details unavailable in reviewed sources] | Local processing can keep source documents within the operator’s environment, but telemetry, remote model downloads, logs, caches, and serving infrastructure still need review. [Inference] |
| **Licensing** | API access and self-hosting are governed by Mistral’s applicable service/enterprise terms; API pricing is not a software license for weights. [S1, S4, S5] | “Open source” does not mean all model weights, datasets, optional services, and downstream use have identical rights. Check code and model-card license terms separately for the exact versions, especially for commercial use and redistribution. [S6–S9] |

No total-cost or privacy ranking is justified by the documentation alone. It depends on volume, quality requirements, engineering capacity, hosting environment, contractual terms, and whether human review is required. [Inference]

## 6. Suggested evaluation for `fastdocparse`

### Evaluation design

Build a **document-disjoint** set representative of the project’s target mix. Include born-digital PDFs, scanned and hybrid PDFs, invoices and receipts, multi-column prose, dense and borderless tables, rotated pages, multilingual pages, and image-heavy documents. Keep a held-out set so model/prompt/configuration tuning does not leak into final evaluation. [Recommendation]

Record separate measures rather than one blended score:

1. **Text:** character/word error rate or field-level exact match, with normalization rules stated.
2. **Layout:** region detection/label scores and box overlap, where ground truth exists.
3. **Reading order:** pairwise precedence accuracy or another explicitly defined order metric; permit ambiguous/alternative order where appropriate.
4. **Tables:** topology and cell-content metrics separately, including spans, blank cells, row/column alignment, and header association.
5. **Extraction:** exact field accuracy, schema validity, unsupported-value rate, and evidence-grounding precision/recall.
6. **Operations:** page-level latency distribution, throughput, peak memory/VRAM, failure rate, installation footprint, retry behavior, cost at expected volume, and human correction time.

Use one frozen input rendering/preprocessing policy, record versions and parameters, and retain raw output. Where one backend returns geometry and another returns only markup, do not treat unavailable fields as incorrect predictions; report capability coverage separately from accuracy. [Recommendation]

### Integration recommendations (inference)

- Keep native PDF text extraction as a fast path for clean born-digital files; route scanned pages to OCR based on page-level evidence rather than forcing every page through the same costly route.
- Define a backend-neutral intermediate representation with page-local elements, stable IDs, boxes/provenance, source order, derived reading order, table grids, and typed relations. Keep OCR text separate from inferred semantic fields.
- Store original files and raw engine outputs. Treat Markdown, HTML, and schema JSON as consumer views, each with explicit omissions and transformations.
- Make hosted OCR an opt-in backend with visible cost/privacy behavior; make local OCR/layout models optional dependencies rather than prerequisites for the lightweight installation.
- Validate OCR/annotation values against source text or pixels where feasible; retain cited element IDs and spans with extracted fields. Route low-confidence or high-impact fields to review rather than interpreting an uncalibrated score as certainty.
- Benchmark at least one API option, one hybrid local parser (e.g., Docling or Marker), and one generative OCR approach (e.g., olmOCR) on the same local set before selecting defaults. Include Nougat only if scholarly documents are an important target class.

## 7. Evidence gaps and cautions

- The reviewed public Mistral material does not document OCR model architecture/weights in enough detail for a grounded internal-design comparison, nor an API-specific latency guarantee. Confirm contractual/privacy details with the applicable service terms and account configuration. [S1–S5]
- The sources reviewed do not establish a directly comparable independent benchmark of all five named systems on one corpus with one runner and aligned output criteria. Benchmark claims should remain attributed to their authors or vendors. [S5–S13]
- Exact behavior varies by model version, repository release, OCR engine, configuration, and output serializer. Re-check source documentation before deployment; this note describes the dated public evidence reviewed, not an immutable product contract. [S1–S13]
- A system’s ability to emit Markdown, JSON, HTML, or confidence scores does not prove correctness or losslessness. Test the fields and relationships that downstream consumers actually need. [S2, S6, S9, S11]

## Sources

**Mistral**

- **[S1]** Mistral AI, “OCR 4.1,” model documentation, model dated July 16, 2026. https://docs.mistral.ai/models/ocr-4-1
- **[S2]** Mistral AI, “OCR Processor,” Studio document-processing documentation. https://docs.mistral.ai/studio/document-processing/basic_ocr
- **[S3]** Mistral AI, “OCR,” API endpoint reference. https://docs.mistral.ai/api/endpoint/ocr
- **[S4]** Mistral AI, “Pricing,” inference pricing documentation. https://docs.mistral.ai/inference/pricing
- **[S5]** Mistral AI, “Mistral OCR 3”; “Mistral OCR 4,” release announcements (OCR 3 announcement and OCR 4 benchmark/feature claims). https://mistral.ai/news/mistral-ocr-3/ ; https://mistral.ai/news/ocr-4/
- Mistral AI, “Annotations,” Studio document-processing documentation. https://docs.mistral.ai/studio/document-processing/annotations

**Comparison systems and evaluation**

- **[S6]** Datalab, Marker repository and documentation. https://github.com/datalab-to/marker
- **[S7]** Allen Institute for AI, “olmOCR 2: Unit Test-Time Reinforcement Learning for Document OCR,” arXiv:2510.19817 (2025). https://arxiv.org/abs/2510.19817
- **[S8]** Luis Blecher, Guillem Cucurull, Thomas Scialom, and Robert Stojnic, “Nougat: Neural Optical Understanding for Academic Documents,” arXiv:2308.13418 (2023). https://arxiv.org/abs/2308.13418
- **[S9]** A. H. Land and colleagues, “Docling Technical Report,” arXiv:2408.09869 (2024). https://arxiv.org/abs/2408.09869
- **[S10]** Allen Institute for AI, olmOCR repository and olmOCR-Bench documentation. https://github.com/allenai/olmocr
- **[S11]** Docling, “Serialization,” documentation. https://docling-project.github.io/docling/concepts/serialization/
- **[S12]** Docling project, source repository. https://github.com/docling-project/docling
- **[S13]** OpenDataLab, OmniDocBench repository, benchmark and version notes. https://github.com/opendatalab/OmniDocBench

*Citation note:* “Inference” and “Recommendation” labels mark conclusions or proposals synthesized from cited capabilities; they are not vendor guarantees. Prices and product features are time-sensitive and should be revalidated before procurement or release.
