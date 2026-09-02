# Roadmap

What's actively planned or under discussion for fastdocparse. For what's already shipped and how it was built, see [docs/build-history.md](docs/build-history.md).

This list changes as priorities shift, if something here looks stale or wrong, open an issue.

## In progress / actively wanted

- **Real accuracy/latency benchmark** ([#19](https://github.com/pranjalparmar/fastdocparse/issues/19)): the README and build history both make an honest-but-unverified claim, fastdocparse's text-first pipeline should be competitive with vision-LLM tools on clean-to-moderate documents, while losing ground on messy/handwritten/complex-table ones. That claim has never actually been measured. This is the single most important thing missing before making bigger comparison claims publicly.
- **Windows CI coverage**: CI currently only runs on `ubuntu-latest`. One known gap this causes: the ReDoS guard in `grounding.py` relies on `SIGALRM`, which doesn't exist on Windows, so it silently runs with no timeout protection there (see [#28](https://github.com/pranjalparmar/fastdocparse/issues/28), already documented in code and the test now skips cleanly instead of failing red). Adding a `windows-latest` job to the CI matrix would catch this class of issue automatically instead of relying on someone noticing.
- **Cookbook / worked examples**: resume parsing ([#29](https://github.com/pranjalparmar/fastdocparse/issues/29)), bank statement extraction with cross-field validation ([#30](https://github.com/pranjalparmar/fastdocparse/issues/30)), contract/legal clause extraction with source-grounding ([#31](https://github.com/pranjalparmar/fastdocparse/issues/31)). Good first issues for anyone who wants a self-contained, real-world example to build.

## Under discussion, not yet scoped

- **Hosted API** ([#20](https://github.com/pranjalparmar/fastdocparse/issues/20)): a thin HTTP wrapper (likely FastAPI) exposing the same schema-driven extraction as a POST endpoint. Auth, rate-limiting, and hosting are all undecided. Comment on the issue with a proposed approach before starting a PR here, this one is big enough to need alignment first.
- **Hybrid OCR+VLM escalation for ungrounded fields**: an idea to escalate a field to a vision-LLM pass specifically when grounding fails (i.e. the field-level confidence signal from Phase 2 already tells you exactly which fields are worth spending extra compute on), instead of running every document through a vision model unconditionally. Field-level localization would help target the escalation to just the relevant region of the page rather than the whole document. This is a genuinely interesting direction but hasn't been scoped into concrete tasks yet, treat it as a "maybe," not a commitment.

## Won't do (for now)

- Vision-LLM-first extraction as the default path. The whole point of the current design is avoiding GPU/vision-model cost for documents that don't need it; if the benchmark above shows that trade-off isn't holding up on a meaningful class of documents, this could get revisited, but it's not on the table until then.
