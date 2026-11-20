# CORD v2 manual failure audit

This is a visual/manual review of the 12-record stratified diagnostic sample
(`cord-v2-failure-diagnostics.json`). The overlays were inspected with reference
boxes in green and parser OCR boxes in red. IDs were selected from test results
for diagnosis only; this is not a representative sample and was not used to
tune parser behavior.

## Findings

| Receipt | Visual diagnosis | Main interpretation |
| --- | --- | --- |
| `0098` | Receipt is partly covered by a strap, and several OCR boxes combine left-side labels with item/amount text on the same rows. | Occlusion plus mixed columns and annotation segmentation. Not a pure recognizer failure. |
| `0068` | Small, blurred receipt. OCR finds the main rows, but joins text and amounts differently from the reference boxes and misreads punctuation/digits. | Both recognition difficulty and box/grouping mismatch. |
| `0054` | OCR reads several item/total strings across a full row, while CORD labels names and amounts as separate boxes. A stray top-page OCR box is also present. | Strong annotation-granularity mismatch; some extra detection. |
| `0076` | Blurred receipt; OCR reads menu names and prices together across rows, where reference annotations split them. Some symbol/character confusions remain. | Annotation-granularity mismatch plus recognition noise. |
| `0038` | Many rows are faint. OCR joins item and price columns and misses quantity tokens; a few totals are read accurately. | Low image quality, missed small text, and label granularity mismatch. |
| `0039` | Very faint/low-contrast receipt with dense tiny print. Parser recovers only a small fragment. | Clear genuine OCR/visibility failure, not explained by grouping alone. |
| `0093` | Blurred receipt. OCR often separates label and amount that CORD groups in one line; one amount is also misrecognized. | Mixed annotation-granularity and recognition error. |
| `0077` | Most item and total rows align closely. The main missed content is a small quantity marker (`1 x`). | Mostly successful; small-token recall issue. |
| `0064` | Several rows align; one item name is misread and the item/price region is grouped differently from its labels. | Recognition error plus annotation/grouping mismatch. |
| `0062` | Main descriptions and amounts align well. Two small quantity markers are not detected. | Mostly successful; small-token recall issue. |
| `0015` | Main descriptions, amounts, and totals align well. Two small quantity markers are missing. | Mostly successful; small-token recall issue. |
| `0043` | Product, quantity, and price are emitted as one OCR row, while CORD has separate annotation segments; most totals are readable. | Strong annotation-granularity mismatch. |

## What this changes in the diagnosis

The earlier geometry-only split/merge heuristics overstate the evidence for
parser grouping errors. Several missed one-to-one line matches are visibly
plausible OCR rows whose boxes span multiple annotation segments (for example,
description plus price); in those cases the evaluator penalizes the larger OCR
box even when it follows the visual row. This is especially evident on `0054`,
`0076`, `0038`, `0093`, and `0043`. In contrast, `0039` is an unambiguous hard
recognition/visibility failure, and `0077`, `0062`, and `0015` are largely good
results with a few tiny quantity marks missed.

Therefore the CORD score should be read as **agreement with `valid_line` box
segmentation plus OCR**, not a clean measure of semantic line grouping. The
validation sweep was also inconclusive as a production direction: it changed
box grouping, but did not improve validation F1 or CER. No parser behavior was
changed.

## Recommended next evaluation step

Before another grouping change, augment receipt evaluation with separately
reported measures that reduce this conflation:

1. OCR text quality on spatially aligned text, with the existing line metric
   retained and explicitly named annotation-segment localization.
2. Word/token-level recognition and localization, where references are the
   CORD word quadrilaterals rather than their parent `valid_line` envelopes.
3. A small reviewed set for genuine missed text, false OCR, and legibility, with
   annotation-granularity disagreements recorded as their own category.

Only then target actual recognition or detection misses. Keep CORD test reserved
for a final evaluation after the metric and change are frozen.
