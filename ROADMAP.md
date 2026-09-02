# Roadmap

What's actively planned or under discussion for fastdocparse. For what's already shipped and how it was built, see [docs/build-history.md](docs/build-history.md).

This list changes as priorities shift, if something here looks stale or wrong, open an issue.

## In progress / actively wanted

- **Real accuracy/latency benchmark** ([#19](https://github.com/pranjalparmar/fastdocparse/issues/19)): the README and build history both make an honest-but-unverified claim, fastdocparse's text-first pipeline should be competitive with vision-LLM tools on clean-to-moderate documents, while losing ground on messy/handwritten/complex-table ones. That claim has never actually been measured. This is the single most important thing missing before making bigger comparison claims publicly.
- **Windows CI coverage**: CI currently only runs on `ubuntu-latest`. One known gap this causes: the ReDoS guard in `grounding.py` relies on `SIGALRM`, which doesn't exist on Windows, so it silently runs with no timeout protection there (see [#28](https://github.com/pranjalparmar/fastdocparse/issues/28), already documented in code and the test now skips cleanly instead of failing red). Adding a `windows-latest` job to the CI matrix would catch this class of issue automatically instead of relying on someone noticing.
- **Cookbook / worked examples**: resume parsing ([#29](https://github.com/pranjalparmar/fastdocparse/issues/29)), bank statement extraction with cross-field validation ([#30](https://github.com/pranjalparmar/fastdocparse/issues/30)), contract/legal clause extraction with source-grounding ([#31](https://github.com/pranjalparmar/fastdocparse/issues/31)). Good first issues for anyone who wants a self-contained, real-world example to build.

## Under discussion, not yet scoped

- **Hosted API** ([#20](https://github.com/pranjalparmar/fastdocparse/issues/20)): a thin HTTP wrapper (likely FastAPI) exposing the same schema-driven extraction as a POST endpoint. Auth, rate-limiting, and hosting are all undecided. Comment on the issue with a proposed approach before starting a PR here, this one is big enough to need alignment first.
- **Hybrid OCR+VLM escalation for ungrounded fields**: cheap-extraction-first with a heavier VLM fallback isn't a novel pattern on its own, retry-with-fallback pipelines that escalate low-confidence fields to a VLM before human review already exist elsewhere. What fastdocparse could add that's less common as a polished abstraction is *field-level* escalation driven directly by the grounding signal it already computes: instead of re-running a whole document through a vision model when something looks wrong, escalate only the specific field that failed grounding, and only after trying to localize where the correct answer actually is on the page.

  Rough shape: `PyMuPDF/RapidOCR (text + bbox)` → `LLM extraction` → `field-level grounding` → *(ungrounded field)* → `spatial bbox search for the right region` → `region retry` → *(still failing)* → `layout detector` → `VLM crop` → *(still failing)* → `whole-page VLM` → `human review`. Each rung only gets used if the cheaper one before it didn't resolve the field, so cost scales with how much a given document actually needs, not with a fixed vision-model-for-everything policy.

  Known hard problems, not yet solved, before this is anything more than an idea:
  - **Crop discovery**: an ungrounded total tells you a field is suspicious, not where the correct value actually is on the page.
  - **Context loss**: cropping too tightly can cut out the label that would've distinguished subtotal, total, and amount due.
  - **Bad OCR breaks localization too**: if OCR missed the relevant text outright, a text-based bbox search has nothing to search against.
  - **Confidence calibration**: a high-confidence extraction can still be wrong, and a flagged/low-confidence one can still be right, grounding is a useful signal, not a ground truth.
  - **Result reconciliation**: if the OCR+LLM pass says one number and the VLM crop says another, there's no rule yet for which one wins.
  - **Cost crossover**: enough retries and crops on one field can end up costing more than a single whole-page VLM pass would have, at which point the "escalate progressively" premise stops paying off.

  Not scoped into concrete tasks yet, this is a "maybe, if the numbers support it," not a commitment. A reasonable first experiment (not yet started): pick one field type (e.g. `invoice_total`), preserve the existing PyMuPDF/RapidOCR bounding boxes, deliberately construct failing cases, and measure how often bbox-based localization alone can find the correct region before any VLM call is needed at all. That result would decide whether the rest of the ladder is worth building.

## Won't do (for now)

- Vision-LLM-first extraction as the default path. The whole point of the current design is avoiding GPU/vision-model cost for documents that don't need it; if the benchmark above shows that trade-off isn't holding up on a meaningful class of documents, this could get revisited, but it's not on the table until then.
