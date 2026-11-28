# Build Reliable Document Structure Before Markdown

Systems that produce Markdown, HTML, or JSON do not get there through text recognition alone: they combine native PDF extraction or OCR with layout analysis, reading-order inference, specialized table/figure handling, and a serializer. **No system guarantees perfect structure**; a readable export can still omit words, scramble columns, flatten merged cells, or invent a relationship. As of **November 12, 2026**, Mistral documents OCR 4.1 as its current OCR model, while commercial services and open-source converters expose different mixes of recognition, structure, and serialization. The key design lesson for this repository is to preserve source evidence and inferred structure separately, then keep its current two-phase design: parse a grounded document representation first and perform task-specific LLM extraction over that representation second. The repository’s own experiments also argue for a staged path: native PDF extraction is the strongest current route, a CORD grouping sweep did not improve results, and the scanned-table cTDaR baseline currently emits no tables.

## “OCR” output is the result of distinct tasks

The phrase “perfect structure” bundles several problems that systems solve separately, sometimes with separate models and sometimes in a joint model. **Recognition** asks what characters or words appear; **layout analysis** locates regions and classifies them as text, heading, table, figure, or other visual types; **reading-order reconstruction** links those regions into one or more traversals; **table structure recognition** recovers rows, columns, cells, spans, and possibly header roles; **semantic extraction** maps evidence to task fields such as invoice totals; and **serialization** renders the result as Markdown, HTML, or JSON. Success at one stage does not establish success at the next. For example, a transcript can be accurate while its columns are interleaved, and a plausible table can have incorrect cell text or spans. [1]–[4]

The pipeline starts differently depending on the source. A born-digital PDF often already contains positioned glyphs and text, so a parser can extract those directly and group them using coordinates, font properties, and whitespace. But PDF content order is not necessarily reading order, and the format does not generally encode paragraphs or semantic hierarchy. Scanned pages instead require rendering, image cleanup such as orientation correction or deskew, text-region detection and recognition; hybrid PDFs can need both routes on the same page. Classic OCR engines such as Tesseract identify text and provide locations, but do not by themselves reconstruct a complete logical document tree. [5], [6]

After recognition, a parser must infer how words belong to lines and regions, which region precedes another, and whether a spatial arrangement is a table or merely aligned prose. Simple top-to-bottom sorting and geometric rules are useful for straightforward pages, but multicolumn layouts, sidebars, captions, and independent flows can make a single total order misleading. Recent reading-order work therefore models successor relations as a directed acyclic graph, with a linear sequence treated as one useful projection rather than the only truth. Similarly, table extraction is a sequence of distinct jobs: detect the table, infer the grid and cell spans, assign recognized text to cells, then optionally classify headers or other functions. [7]–[9]

Finally, Markdown is a **view**, not the document’s ground truth. Pipe tables are readable but have no native way to represent all merged-cell spans, coordinates, confidence, figure assets, or uncertain relationships. HTML can express row and column spans, while structured JSON or a typed internal model can retain richer geometry and provenance. Those formats do not make recognition infallible either: they only make different information available to consumers. A robust system keeps its internal evidence-rich representation and makes any Markdown/HTML export’s omissions explicit. [2], [10]

## Commercial systems return different kinds of evidence

**Mistral OCR is a managed document-processing API, not a disclosed architecture blueprint.** As of the research cutoff, Mistral’s model documentation identifies `mistral-ocr-4-1`, dated July 16, 2026, as OCR 4.1; its listed price is $4 per 1,000 pages, or $5 per 1,000 with annotations. OCR 3 (`mistral-ocr-2512`) remains listed at $2 per 1,000 pages. The documented OCR flow returns per-page Markdown and can expose structured page/block data, including paragraph labels, boxes, reading order, and confidence signals; tables can be returned in HTML or Markdown. Annotations add a distinct schema-oriented extraction step. Mistral has not disclosed enough of the model architecture or evaluation setup in the reviewed public sources to say precisely how its recognizer works internally, and vendor benchmark claims should be understood as vendor-reported, not as a universal ranking. [11]–[14]

Other commercial document AI products make their intermediate evidence more explicit. Google Document AI’s standard response represents OCR text through text and page anchors, geometry, and layout objects; its Layout Parser is documented as a hybrid process combining specialized OCR and Gemini generative capabilities to organize, annotate, and chunk a document. Azure Document Intelligence describes its Layout API as enhanced OCR plus deep-learning models and returns page, paragraph, word, table-cell, and figure information; its Markdown content can embed HTML tables to express merged or multirow headers. Amazon Textract exposes a graph of typed blocks such as `WORD`, `TABLE`, `CELL`, and `KEY_VALUE_SET`, linked by relationship IDs, which applications traverse to build higher-level output. ABBYY Vantage separates physical layout and logical content in OCR JSON, with a further schema-driven extraction result for named fields. These are richer contracts than a single text string, but the documented capabilities do not guarantee the correctness of a particular layout or field. [15]–[18]

Those response models also reveal why “the OCR gave me Markdown” is not a useful quality specification. A Markdown export may be easy to inspect, but the JSON or block graph is where a service can preserve the source location of text, a cell’s row/column index, a field’s supporting span, or a confidence score. Confidence is a local service signal—not a probability that can be compared directly across vendors without calibration on the same data. Vendor APIs also differ in product version, cost, deployment, and what their output contract preserves; those details should be rechecked at integration time. [15]–[18]

## Open systems and research choose different tradeoffs

Open-source systems span lightweight recognition engines, modular parsers, and image-to-markup models. **Tesseract** and **EasyOCR** primarily recognize text and return coordinates (and, depending on the format, confidence-like scores); they need additional layout and structure logic for full document conversion. **PaddleOCR** layers detection, orientation correction, and recognition in its OCR path, while PP-StructureV3 adds layout analysis and element-specific processing such as table recognition and reading-order recovery. PaddleOCR-VL combines a compact vision-language model with layout detection and can serialize results to JSON and Markdown. [19]–[21]

**Docling, Marker, and MinerU are better understood as conversion pipelines.** Docling’s documented approach combines PDF-native text and positions with rendered-page analysis, layout and table models, OCR when needed, post-processing, and a typed document representation. It can then serialize that representation to Markdown, HTML, or JSON. Its report describes aligning predicted table structure with PDF text instead of asking OCR to re-read legible native text; it also distinguishes richer structured output from lossy Markdown/HTML views. Marker similarly combines document/PDF extraction with layout and OCR components, with optional LLM-based processing or repair depending on release and configuration. MinerU also separates intermediate structured results from consumer-facing Markdown, HTML, and other renderings. **olmOCR 2 and Nougat take a more generative image-to-markup route**: olmOCR uses a vision-language model trained for document OCR and markup, while Nougat specializes in scholarly documents and scientific markup. These approaches can produce coherent structure in fewer explicit stages, but a generated transcript is not automatically a coordinate-preserving document graph. Nougat’s academic-paper focus should not be mistaken for broad business-document coverage. [22]–[25], [34]

The research literature explains the component choices. LayoutLM-family models encode OCR tokens together with 2-D positions and, in later versions, visual features; they support tasks such as form labeling and layout analysis, but are not generic PDF-to-Markdown serializers. Donut demonstrates OCR-free, image-to-structured-output prediction for defined document tasks, trading away explicit OCR token coordinates by default. Table Transformer and TableFormer specialize in table-region and table-grid inference; their visual structures still need text from PDF extraction or OCR to populate cells. LayoutReader learns reading order from text and geometry, while relation-based work allows multiple flows where one sequence is too restrictive. Dolphin’s “analyze then parse” approach first identifies ordered layout elements and then parses those elements with specialized prompts. The unifying theme is decomposition or task specialization: each method contributes evidence for a particular stage, not a proof that all stages are right. [7]–[9], [26]–[30]

Benchmarks reinforce the caution. OmniDocBench evaluates multiple document-parsing properties and reports category-specific weaknesses, including harder reading order in complex layouts and resolution/token limits on dense pages. Table metrics such as GriTS separate topology, content, and location; a character error rate cannot replace them. OCR and document-QA benchmarks also test selected recognition or answer tasks, not necessarily exhaustive transcription, correct order for every block, or lossless conversion. The reviewed literature does not supply one independent, same-version, same-runner comparison of all the named commercial and open systems on a representative corpus. A quality claim should therefore specify the document types, stage, metric, model/version, and output contract rather than assert “perfect OCR.” [3], [8], [31], [32]

## Repository evidence points to a native-first two-phase design

For this repository, keep **phase one as parsing**: create a geometry-aware, source-grounded representation of what the file contains and how elements relate. Keep **phase two as extraction**: ask the LLM or deterministic schema logic for task-specific fields, and require those values to point back to parsed elements, text spans, or cells. The second phase should interpret evidence, not silently rewrite the transcript or become the source of generic page structure. Native PDF text and geometry should remain the preferred path when trustworthy; scanned pages and regions should be routed to optional OCR, while difficult layouts or tables can be escalated to optional specialist models. This resembles the evidence-preserving hybrid used by modular converters without making a heavy visual model mandatory. [2], [10], [22], [26]

The existing evidence argues for measured, incremental work rather than changing grouping thresholds or selecting a new backend from product claims. The earlier repository report records a **36-PDF, mostly born-digital customs/invoice/packing-list-like corpus**, including six scan-named files and twelve two-page documents; it also identifies native positioned PDF extraction as the project’s strongest current route. On CORD v2 validation, the baseline line F1 was 0.468 at IoU 0.50 with 89.7% CER. A 16-setting spatial grouping sweep raised localization recall but reduced micro F1 to 0.438, lowered precision to 0.452, worsened CER to 100.5%, and increased predicted line count from 843 to 1,146. The tested grouping rule should therefore **not** be adopted; it did not improve the measured outcome. In the seeded 30-document cTDaR sample, the current parser emitted **0/35 table regions and 0/8,975 cells**. That zero indicates a missing scanned-table capability, not a weak score from a table model that has already been implemented. These are scoped baselines, not evidence of generalization beyond their datasets. [33]

The next steps should follow that evidence. **First**, strengthen the existing parse contract without disturbing the native path: add stable element identifiers, explicit source order distinct from derived reading order, page coordinate-frame/rotation metadata, typed relations, and round-trip JSON tests. Preserve table grids and figures as elements, and make Markdown a generated projection. **Second**, close the scanned-input evidence gap by retaining OCR word boxes, confidence, and coordinate transforms; add page-level diagnostics and a conservative native/OCR/hybrid route. Keep the CORD grouping sweep as a negative result and inspect its errors rather than shipping the tested threshold. **Third**, address cTDaR with an optional scanned-table detector/structure-recognition backend, aligning predicted cells to OCR words; measure detection, grid topology, cell content, spans, and relation recall separately. **Fourth**, compare candidate hosted and local systems on a fixed, document-disjoint sample that reflects the repository’s actual target, tracking quality alongside latency, install/dependency burden, licensing, and data-handling requirements. Only enable a learned backend by default if that evaluation justifies it. [2], [8], [10], [33]

## Conclusion

Document systems produce useful Markdown by converting source observations into inferred layout and relationships, then choosing a serialization. The durable design choice is not which model promises the prettiest output; it is whether the parser retains enough evidence to detect, explain, and correct its own mistakes. For this repository, that means preserving native text first, adding scanned OCR and table structure as measured optional capabilities, and keeping semantic extraction grounded in the parsed representation.

## References

[1] Linke Ouyang *et al*., “OmniDocBench: Benchmarking Diverse PDF Document Parsing with Comprehensive Annotations,” *Proc. CVPR*, 2025. [Online]. Available: https://openaccess.thecvf.com/content/CVPR2025/html/Ouyang_OmniDocBench_Benchmarking_Diverse_PDF_Document_Parsing_with_Comprehensive_Annotations_CVPR_2025_paper.html

[2] IBM Research, “Docling Technical Report,” arXiv:2408.09869, 2024. [Online]. Available: https://arxiv.org/abs/2408.09869

[3] Christoph Auer *et al*., “Docling Technical Report,” full text. [Online]. Available: https://arxiv.org/html/2408.09869v5

[4] Microsoft, “Document layout analysis – Document Intelligence.” Updated May 1, 2026. [Online]. Available: https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout?view=doc-intel-4.0.0

[5] PyMuPDF, “Text Extraction Recipes.” [Online]. Available: https://pymupdf.readthedocs.io/en/latest/recipes-text.html

[6] R. Smith, “An Overview of the Tesseract OCR Engine,” in *Proc. ICDAR*, 2007, doi: 10.1109/ICDAR.2007.4376991. [Online]. Available: https://tesseract-ocr.github.io/docs/tesseracticdar2007.pdf

[7] C. Zhang *et al*., “Modeling Layout Reading Order as Ordering Relations for Visually-rich Document Understanding,” in *Proc. EMNLP*, 2024. [Online]. Available: https://aclanthology.org/2024.emnlp-main.540/

[8] Z. Smock, R. Pesala, and R. Abraham, “PubTables-1M: Towards Comprehensive Table Extraction From Unstructured Documents,” in *Proc. CVPR*, 2022. [Online]. Available: https://openaccess.thecvf.com/content/CVPR2022/html/Smock_PubTables-1M_Towards_Comprehensive_Table_Extraction_From_Unstructured_Documents_CVPR_2022_paper.html

[9] Z. Smock, R. Pesala, and R. Abraham, “GriTS: Grid Table Similarity Metric for Table Structure Recognition,” arXiv:2203.12555, 2022. [Online]. Available: https://arxiv.org/abs/2203.12555

[10] Docling, “Serialization.” [Online]. Available: https://docling-project.github.io/docling/concepts/serialization/

[11] Mistral AI, “OCR 4.1,” model documentation, July 16, 2026. [Online]. Available: https://docs.mistral.ai/models/ocr-4-1

[12] Mistral AI, “OCR Processor,” Studio document-processing documentation. [Online]. Available: https://docs.mistral.ai/studio/document-processing/basic_ocr

[13] Mistral AI, “OCR,” API endpoint reference. [Online]. Available: https://docs.mistral.ai/api/endpoint/ocr

[14] Mistral AI, “Pricing,” inference pricing documentation; and “Mistral OCR 3” / “Mistral OCR 4,” release announcements. [Online]. Available: https://docs.mistral.ai/inference/pricing ; https://mistral.ai/news/mistral-ocr-3/ ; https://mistral.ai/news/ocr-4/

[15] Google Cloud, “Document AI Document resource (REST).” [Online]. Available: https://docs.cloud.google.com/document-ai/docs/reference/rest/v1/Document

[16] Google Cloud, “Process documents with Gemini layout parser.” [Online]. Available: https://docs.cloud.google.com/document-ai/docs/layout-parse-chunk

[17] Amazon Web Services, “Text Detection and Document Analysis Response Objects” and “Tables,” *Amazon Textract Developer Guide*. [Online]. Available: https://docs.aws.amazon.com/textract/latest/dg/how-it-works-document-layout.html ; https://docs.aws.amazon.com/textract/latest/dg/how-it-works-tables.html

[18] ABBYY, “OCR Skill JSON Schema” and “Understanding your results,” *Vantage Developer Documentation*. [Online]. Available: https://docs.abbyy.com/vantage/developer/output/json/ocr/ocr-json-schema ; https://docs.abbyy.com/vantage/getting-started/results

[19] Tesseract OCR Project, “Command Line Usage.” [Online]. Available: https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html

[20] JaidedAI, “EasyOCR Repository and API Documentation.” [Online]. Available: https://github.com/JaidedAI/EasyOCR ; https://www.jaided.ai/easyocr/documentation/

[21] C. Cui *et al*., “PaddleOCR 3.0 Technical Report,” arXiv:2507.05595, 2025; PaddlePaddle, “Tutorial for PaddleOCR-VL Series Pipelines.” [Online]. Available: https://arxiv.org/abs/2507.05595 ; https://paddlepaddle.github.io/PaddleX/latest/en/pipeline_usage/tutorials/ocr_pipelines/PaddleOCR-VL.html

[22] Datalab, “Marker Repository and Documentation.” [Online]. Available: https://github.com/datalab-to/marker

[23] A. H. Land *et al*., “Docling Technical Report,” arXiv:2408.09869, 2024. [Online]. Available: https://arxiv.org/abs/2408.09869

[24] J. Poznanski, L. Soldaini, and K. Lo, “olmOCR 2: Unit Test-Time Reinforcement Learning for Document OCR,” arXiv:2510.19817, 2025. [Online]. Available: https://arxiv.org/abs/2510.19817

[25] L. Blecher, G. Cucurull, T. Scialom, and R. Stojnic, “Nougat: Neural Optical Understanding for Academic Documents,” in *Proc. ICLR*, 2024. [Online]. Available: https://arxiv.org/abs/2308.13418

[26] Y. Xu *et al*., “LayoutLM: Pre-training of Text and Layout for Document Image Understanding,” in *Proc. ACM SIGKDD*, 2020, doi: 10.1145/3394486.3403172. [Online]. Available: https://arxiv.org/abs/1912.13318

[27] Y. Huang *et al*., “LayoutLMv3: Pre-training for Document AI with Unified Text and Image Masking,” in *Proc. ACM Multimedia*, 2022. [Online]. Available: https://arxiv.org/abs/2204.08387

[28] G. Kim *et al*., “OCR-Free Document Understanding Transformer,” in *Proc. ECCV*, 2022. [Online]. Available: https://arxiv.org/abs/2111.15664

[29] A. Nassar *et al*., “TableFormer: Table Structure Understanding With Transformers,” in *Proc. CVPR*, 2022. [Online]. Available: https://openaccess.thecvf.com/content/CVPR2022/html/Nassar_TableFormer_Table_Structure_Understanding_With_Transformers_CVPR_2022_paper.html

[30] H. Feng *et al*., “Dolphin: Document Image Parsing via Heterogeneous Anchor Prompting,” *Findings of ACL*, 2025. [Online]. Available: https://aclanthology.org/2025.findings-acl.1130/

[31] L. Fu *et al*., “OCRBench v2: An Improved Benchmark for Evaluating Large Multimodal Models on Visual Text Localization and Reasoning,” *NeurIPS 2025 Datasets and Benchmarks Track*. [Online]. Available: https://proceedings.neurips.cc/paper_files/paper/2025/hash/8c2e6bb15be1894b8fb4e0f9bcad1739-Abstract-Datasets_and_Benchmarks_Track.html

[32] OpenDataLab, “OmniDocBench Repository and Version Notes.” [Online]. Available: https://github.com/opendatalab/OmniDocBench

[33] “Scanned table and receipt baselines,” repository research report, Nov. 2026. [Online]. Available: [reports/Scanned structure and receipt baselines.md](Scanned%20structure%20and%20receipt%20baselines.md); see also [reports/General document parsing 2026.md](General%20document%20parsing%202026.md).

[34] MinerU, “Output Format and Result Contract.” [Online]. Available: https://opendatalab.github.io/MinerU/reference/output_files/
