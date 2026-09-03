# Contributing

## Workflow

**No direct pushes to `main`.** This is enforced by branch protection (including for repo admins), not just a convention. All changes go through a pull request:

```bash
git checkout -b your-branch-name
# make your changes
git push -u origin your-branch-name
gh pr create   # or open the PR on github.com
```

CI (`test (3.10)`, `test (3.12)`, `build`, `Analyze (python)`) must pass before a PR can merge, and that's also enforced, not optional. Once it's green, merge the PR (squash or regular merge, your call) rather than merging locally and pushing `main` directly.

## Claim an issue before starting work

**Comment on the issue before you start coding**, something as simple as "I'll take this" works. Wait for a maintainer to assign it to you or give a thumbs-up before opening a PR.

This isn't bureaucracy for its own sake: two people have independently opened PRs for the same issue here because neither knew the other was working on it. Both had solid work, but one submission necessarily didn't land as-is, and that's a worse outcome than a 10-second comment would have prevented. If an issue is already assigned, please pick a different one rather than opening a competing PR for it.

## Setup

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -e ".[dev]"
```

## Running the tests

```bash
pytest -v
```

All tests should pass before you open a PR. CI runs this automatically on every push and PR, but running it locally first saves a round-trip.

## Before opening a PR

- **Add or update tests for anything you change.** This project has been through several rounds of "found a real bug, added a regression test for it": that pattern is the standard here, not the exception. A behavior change with no test is treated as unverified.
- **Run the full suite**, not just the file you touched, since several modules interact (e.g. changes to `grounding.py`'s `check_substring` affect both `parser.py`'s merge logic and its final grounding check).
- **Keep the docs in sync.** If you change a CLI flag, a `Schema`/`Field` option, or the output shape, update the relevant file in `docs/` and/or `README.md` in the same PR. Stale docs have caused real confusion here before (see git history).
- **Add a changelog entry when behaviour changes for a user.** A new flag, a changed message, a fixed bug: one line under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md), in the right group (Added / Changed / Fixed / Documentation), linking the PR. Internal refactors, test-only changes and CI edits do not need one — the question to ask is whether somebody deciding whether to upgrade would want to know. Writing it at PR time is the point: reconstructing a release afterwards from PR titles is how a changelog ends up guessed rather than recorded.
- **Don't break backward compatibility silently.** `DocumentParser.extract()`'s return shape (a dict with `_meta` + one entry per field) is a public contract: the CLI, `ExtractionResult`, and every test depend on it. If a change requires breaking it, say so explicitly in the PR description rather than letting it happen as a side effect.

## Code style

- No comments explaining *what* code does; names should carry that. Comments are for *why*: a non-obvious constraint, a workaround, a decision that would otherwise look arbitrary.
- Prefer extending an existing module's pattern over introducing a new one. E.g. a new document format is a new function in `parser.py`'s `INGESTION_HANDLERS`-style registry, not a parallel ingestion system; a new validation rule is a new factory function in `grounding.py`, not a new file.
- Config knobs belong in `config.py`'s `ExtractionConfig`, not as hardcoded constants scattered through the pipeline (that was a real bug here once: two disconnected page-limit constants caused silent data loss; see `config.py`'s comment on `max_pages`).

## Project structure & where to contribute

All library code lives under `src/fastdocparse/` (installed as the `fastdocparse` package); `test_*.py` files under `tests/` import from it as `from fastdocparse import ...` / `from fastdocparse.parser import ...`, exactly as an external user would.

For the module map, the data-flow pipeline, the dependency graph between modules, and a diagram of where common contributions plug in, see **[docs/architecture.md](docs/architecture.md)**, kept as one diagram-based doc rather than duplicated here, so it doesn't drift out of sync with a second copy.

New public functionality goes through `src/fastdocparse/__init__.py`'s `__all__`. If it's meant to be used from `from fastdocparse import X`, it needs to be re-exported there, not just defined in its own module.

## Reporting issues

Include: the schema you used (or a minimal repro), the document type (not the document itself if it's sensitive), and the exact error/output vs. what you expected.
