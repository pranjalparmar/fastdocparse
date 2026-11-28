# Separate parsing from grounded extraction

**Snapshot: November 12, 2026.** Docling and LangExtract solve different parts of a document workflow, so neither is a direct stand-in for the other. Docling converts files—especially PDFs and images—into a structured representation by combining extracted text, page images, OCR, layout analysis, reading-order logic, and specialized table processing. LangExtract starts with text that is already available, asks an LLM to find task-specific information in it, and aligns returned extraction strings to character spans in that same text. That alignment is useful evidence, but it is not PDF parsing, page-layout understanding, or proof that an extracted claim is true. For fastdocparse, the most reusable ideas are Docling’s separation of evidence, structure, and output, and LangExtract’s explicit, auditable text-span alignment—not either project’s entire architecture.

## Docling turns pages into a structured document

Docling is a configurable document-conversion framework, rather than a single OCR step. In its standard PDF route, the converter identifies the input and selects a backend and pipeline; the PDF backend can provide both native text with page geometry and rendered page images. The pipeline then processes page evidence: OCR supplies text where needed, layout detection proposes regions such as paragraphs, titles, tables, and figures, postprocessing cleans those proposals, and page assembly associates regions with text. Reading-order processing then arranges the assembled items, while a separate table-structure stage can infer rows, columns, headers, and spans and align them with extracted text. Finally, document assembly produces a typed `DoclingDocument`, which can be serialized or chunked downstream [1–4].

These are logical stages, not a guarantee of one strictly serial execution: the current standard pipeline is threaded, and model choices and options vary by route and configuration. The older technical report remains useful for understanding the lineage—native text and rendered pages, layout detection, table recognition, optional OCR, then assembly—but its original models, settings, and performance numbers should not be mistaken for current defaults. Separate native-PDF and vision-language-model routes also exist; the appropriate behavior depends on which pipeline is selected [2–4].

The important output is not just a string. `DoclingDocument` keeps typed content—such as text, tables, pictures, and key-value items—alongside hierarchy, reading order, and layout/provenance information when available. That lets later consumers make different views of the same parse: for example, HTML can retain table spans, while Markdown flattens some of that structure. In other words, serialization is a view over the document representation, not necessarily the canonical parse itself [5–6].

## LangExtract starts after file parsing

LangExtract’s core API accepts text strings or `Document` objects containing text, an optional ID, and context. Its role is to extract user-specified information from that text, not to turn a PDF into text. An optional URL-fetching path downloads text-like response content; it is not a PDF conversion backend. The reviewed core input and output types do not provide page images, bounding boxes, or a PDF layout tree [7–9].

Its extraction flow is: take the supplied text; divide it into sentence- and token-aware character-bounded chunks; construct prompts from the user’s extraction instructions, examples or output schema, context, and each chunk; call a selected LLM provider in batches, optionally in multiple passes; parse the model’s structured response; and search for each returned extraction string in its source chunk. The aligner records token and character intervals and an alignment status, then offsets successful matches back into the full input text. If it cannot locate a returned string, its character interval can remain absent. Results are annotated text documents with extraction classes, values, optional attributes, and text offsets—not page-level document structures [7–10].

That is what “grounding” means here: an extraction can be tied to a matching span in the exact text passed to LangExtract. It does **not** show that upstream OCR was accurate, that the text was assigned to the right page or column, or that an inferred attribute is factually supported. Fuzzy alignment is a matching aid, not semantic verification; and offsets only map back to a visual page if an upstream parser preserves a separate correspondence between its text and page elements. The core library’s PDF/layout boundary is visible in its distinct integration discussion: Docling or another parser can supply text first, but that does not make PDF parsing a built-in LangExtract function [8–11].

## Their representations preserve different evidence

The projects’ outputs are complementary, not competing versions of the same parse. Docling represents a document: page-derived items, layout, hierarchy, order, and provenance. LangExtract represents task-specific findings over text: each extraction’s class and value, with a character interval when alignment succeeds. A Docling paragraph can be located spatially even before anyone asks a question about it; a LangExtract extraction can answer a question about supplied text without knowing where that text sat on a page. Connecting the two requires an explicit mapping from LangExtract’s text offsets to the parser’s items and geometry [5, 8, 11].

This also clarifies a common misconception: LangExtract does not parse PDFs or analyze page layout. It is an LLM-assisted text extraction and alignment library. Google’s separate Document AI Layout Parser is an example of an actual layout-parsing service; its capabilities should not be attributed to LangExtract just because both are Google projects [11–12]. Likewise, character-level grounding is narrower than page-level provenance: it provides a useful audit handle for a text result, but not an end-to-end guarantee about the original document.

## Borrow the seams, not the whole machinery

For fastdocparse, Docling’s strongest design lesson is to keep the evidence and structure that extraction can use: native PDF text and geometry when available, OCR text with its own provenance, explicit ordering, and typed table or region data where the workload justifies it. A compact intermediate representation with stable item identity, page coordinates, source provenance, and explicit order would keep inspection and extraction connected without making Markdown the only parse result. Returning diagnostics and partial results matters too: an empty native layer, an OCR miss, ambiguous reading order, or a table alignment error should not silently become a confident empty document. The useful pattern is a clear boundary between input evidence, optional recognition, assembly, canonical representation, and serialization—not Docling’s broad format matrix, model stack, or plugin ecosystem [1, 3–6].

LangExtract offers a different reusable pattern: make text-span alignment explicit and inspectable, and distinguish a verbatim source match from a model-generated value that could not be located. That complements fastdocparse’s existing goal of deterministic per-field grounding and validation, but the semantics should stay precise: a match supports traceability to extracted text, not truth or correctness of OCR. There is little reason to copy LangExtract’s few-shot prompting and provider orchestration if fastdocparse’s schema-driven extraction already serves its use case. And neither project’s output should be adopted wholesale: Docling’s complete typed model and LangExtract’s annotated-text schema solve different consumer needs [7–10, 13].

## Start with smaller design questions

Before choosing tools or expanding implementation, work through these questions in order: **What input types and failure cases should fastdocparse support first—digital PDFs, scanned pages, images, or a deliberately smaller subset?** **For each page, which evidence should be retained: native text, OCR text, geometry, rendered image references, or some combination?** **How will the system distinguish source text from OCR, inferred labels, and later corrections?** **What is the smallest useful structure beyond a flat string—ordered blocks, table cells, figures, or only the items current extraction needs?** **When should OCR or other expensive recognition run, and what should the user see when evidence is missing or ambiguous?** **What does “grounded” promise: a match in extracted text, a mapped page region, or both?** **Which output formats can safely discard structure, and which downstream tasks need the richer representation?** These questions turn the comparison into a manageable sequence of decisions; they do not imply that fastdocparse should reproduce either project’s architecture.

## References

[1] Docling, “Architecture,” https://docling-project.github.io/docling/concepts/architecture/ (accessed 2026-11-12).

[2] Auer et al., “Docling Technical Report,” arXiv:2408.09869v5, https://arxiv.org/html/2408.09869v5.

[3] Docling v2.150.0 release notes, https://github.com/docling-project/docling/releases/tag/v2.150.0; standard pipeline source, https://github.com/docling-project/docling/blob/v2.150.0/docling/pipeline/standard_pdf_pipeline.py.

[4] Docling, “OCR in Docling,” https://docling-project.github.io/docling/concepts/OCR/; “Plugins,” https://docling-project.github.io/docling/concepts/plugins/.

[5] Docling, “Docling document,” https://docling-project.github.io/docling/concepts/docling_document/.

[6] Docling, “Serialization,” https://docling-project.github.io/docling/concepts/serialization/; “Chunking,” https://docling-project.github.io/docling/concepts/chunking/.

[7] LangExtract, README, https://github.com/google/langextract/blob/main/README.md; extraction API, https://github.com/google/langextract/blob/main/langextract/extraction.py; core data types, https://github.com/google/langextract/blob/main/langextract/core/data.py.

[8] LangExtract, chunking, https://github.com/google/langextract/blob/main/langextract/chunking.py; annotation, https://github.com/google/langextract/blob/main/langextract/annotation.py; resolver, https://github.com/google/langextract/blob/main/langextract/resolver.py.

[9] LangExtract, I/O, https://github.com/google/langextract/blob/main/langextract/io.py.

[10] LangExtract, prompting, https://github.com/google/langextract/blob/main/langextract/prompting.py; providers, https://github.com/google/langextract/blob/main/langextract/providers/README.md.

[11] LangExtract issue #184, Docling integration and native document support discussion, https://github.com/google/langextract/issues/184.

[12] Google Cloud, “Layout Parser,” https://docs.cloud.google.com/document-ai/docs/layout-parse-chunk.

[13] fastdocparse README, https://github.com/pranjalparmar/fastdocparse; architecture overview, https://github.com/pranjalparmar/fastdocparse/blob/main/docs/architecture.md.

**Version note:** This report snapshots Docling v2.150.0 (released November 10, 2026) and LangExtract v1.7.2 (released November 7, 2026) as of November 12, 2026. Several LangExtract and Docling documentation/source links point to moving `main` or live documentation; verify version-specific contracts before relying on defaults [3, 14].

[14] LangExtract v1.7.2 release, https://github.com/google/langextract/releases/tag/v1.7.2.
