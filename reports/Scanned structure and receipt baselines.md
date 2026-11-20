# Scanned table and receipt baselines

These runs extend the FUNSD OCR baseline with one receipt set and one table
structure set. They measure the current parser without adding a model or tuning
against the evaluation samples. Downloaded datasets were kept outside the
repository.

## Results

| Dataset / task | Evaluation data | Main result |
| --- | --- | --- |
| FUNSD scanned-form OCR/line localization | Official test split, 50 pages | Line F1 0.835 at IoU >= 0.50; F1 0.721 at IoU >= 0.75; matched-line CER 30.2% |
| CORD v2 receipt OCR/line localization | Official test split in the Hugging Face export, 100 receipts | Line F1 0.424 at IoU >= 0.50; F1 0.381 at IoU >= 0.75; CER 102.7%; annotation-line localization recall 0.363 |
| cTDaR table-region/cell baseline | Seeded 30-document sample from public Track B1 training annotations | 0/35 table regions and 0/8,975 cells emitted; cTDaR polygon table-detection recall 0 and Track B1 adjacency-relation recall 0 |

Per-document outputs:

- [FUNSD OCR](funsd-ocr-baseline.json)
- [CORD v2 receipts](cord-v2-test-baseline.json)
- [CORD v2 validation grouping sweep](cord-v2-validation-grouping-sweep.json)
- [CORD v2 validation baseline](cord-v2-validation-baseline.json)
- [cTDaR Track B1](ctdar-trackb1-baseline.json)

## What the numbers mean

CORD's `valid_line` labels are word quadrilaterals grouped into lines, with
`group_id` and `row_id` metadata. We form an axis-aligned envelope per annotated
line and measure OCR text and localization. Grouping recall means the parser
recovered an annotated line as a spatially matching OCR block; it does **not**
mean semantic group or menu-row assignment was evaluated. The CER exceeds 100%
because edit errors include inserted text from unmatched OCR lines as well as
errors on matched lines. This reveals substantial mismatch on this receipt test
set, but is not an official CORD leaderboard score.

For cTDaR, the parser currently emits no table/cell elements from the scanned
images, so the zero result reflects a missing capability, not a weak learned
model. The report includes envelope diagnostics and polygon-IoU table detection
plus Track B1's directed adjacency-relation scoring, following the public
evaluator's matching rules. With no predicted tables/relations, table detection
and relation recall are zero; relation precision is undefined. This remains a
30-document training/development sample, not a held-out test split. cTDaR has
distinct modern and historical annotation conventions; the run used Track B1's
historical table-structure subset. The dataset repository does not clearly
state a license in its README, so verify usage rights with the organizers
before redistribution or other non-research use. The adjacency-relation
reimplementation was cross-checked against the public Python evaluator on all
35 annotated tables in this sample; the relation sets matched exactly.

## Receipt error triage (CORD v2)

To make the next engineering step evidence-based, the diagnostic runner selected
12 of the 100 test receipts: five with the highest per-document CER, five with
the lowest line F1, and five with the highest line F1 (overlap between groups
leaves 12 unique documents). In this sample:

- **8/12** had fewer predicted blocks than reference lines, consistent with
  under-detection and/or several lines being over-grouped into a block.
- **7/12** had unmatched OCR blocks that geometrically cover multiple reference
  lines, a heuristic sign of possible line merging.
- **3/12** had reference lines covered by multiple OCR blocks, a heuristic sign
  of possible splitting or fragmentation.
- **3/12** had matched lines in the IoU 0.50–0.75 band, indicating loose boxes.
- **2/12** had a spatially matched line with normalized text error over 25%.

These categories overlap. The split/merge indicators are geometric heuristics,
not labeled ground truth. The sample suggests that **line grouping / box extent**
deserves investigation before changing the OCR recognizer: several difficult
receipts have no spatially matched lines, so recognition CER alone can’t explain
their result. Still, this is a small, intentionally stratified sample and does
not estimate category frequency for all receipts.

The saved, reproducible diagnostic is
[`cord-v2-failure-diagnostics.json`](cord-v2-failure-diagnostics.json); its
sample IDs and per-document counts are recorded there. It stores counts and
metrics, not receipt images or transcripts.

A manual overlay audit of those 12 receipts found that several apparent misses
are **annotation-granularity mismatches**: CORD may split a product row into
separate description and price segments, while OCR returns the visually aligned
row as one block. It also confirmed some genuine hard cases, notably very faint
receipt `0039`, and mostly successful cases with only small quantity marks
missed. See [`cord-v2-manual-failure-audit.md`](cord-v2-manual-failure-audit.md).
This makes the failure categories diagnostic prompts, not ground-truth causes;
the CORD line score conflates OCR with agreement to `valid_line` segmentation.

### Validation-only grouping exploration

The CORD validation Parquet shard was used after the viewer API repeatedly
failed. On 100 validation receipts, the baseline line F1 was 0.468 at IoU 0.50
(CER 89.7%). A post-hoc 16-setting sweep over raw OCR word detections selected
minimum vertical overlap 0.65 and maximum horizontal gap 4 detection heights;
this lowered micro line F1 to 0.438 and raised localization recall to 0.425,
but reduced precision (0.452) and worsened CER to 100.5%. It also increased
predicted line count from 843 to 1,146. Therefore **do not adopt this grouping
setting**: the validation evidence does not support a simple grouping-threshold improvement.
Production parser behavior remains unchanged. The validation baseline and full
sweep, including all parameter settings, are saved separately. The external
Parquet shard and extracted images remain outside the repository.

CORD v2's dataset card states CC BY 4.0. Attribute NAVER CLOVA and follow the
license requirements. FUNSD usage is subject to its non-commercial research and
educational terms; review the current official terms before reuse.

## Reproduction

See the dataset-specific instructions in [`benchmarks/cord/README.md`](../benchmarks/cord/README.md)
and [`benchmarks/ctdar/README.md`](../benchmarks/ctdar/README.md). The CORD
downloaded split and cTDaR sample are not checked in. The cTDaR sample uses
seed 2019; its exact IDs are recorded in the external sample's manifest.

## Interpretation for next work

The two additions confirm the tasks should stay separate. Receipt OCR needs
receipt-specific recognition and carefully evaluated line grouping; this
validation sweep does not support the tested simple post-hoc grouping rule.
Table reconstruction needs a scanned-table detector and structure recognizer.
Benchmark numbers are baselines only and do not establish broad generality
beyond these scanned-form, receipt, and table datasets. Keep the lightweight
native-PDF path intact, implement an optional scanned-table backend behind a
clear interface, and compare changes against fixed test splits before training
or enabling a model by default.
