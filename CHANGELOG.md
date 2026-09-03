# Changelog

All notable changes to fastdocparse are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Dates are the PyPI upload dates, which are what a user upgrading actually sees.

## [Unreleased]

### Added

- `fastdocparse extract --max-pages` to cap how much of a document is read ([#46]).

### Fixed

- `test_extract_command_rejects_unreadable_schema_cleanly` could not establish its
  premise on Windows: `chmod(0o000)` does not remove the owner's read access there,
  so the guard under test never fired and the assertion compared against an
  unrelated credentials error. It now patches `os.access`, which exercises the same
  code path on every platform ([#63]).

## [0.3.0] — 2026-09-02

### Added

- **Local-model support in the code, not only in the README.**
  `FASTDOCPARSE_MODEL` and `FASTDOCPARSE_BASE_URL` are read from the environment,
  and `OPENAI_API_KEY` is now actually checked — the README had always documented
  it and the code never looked ([#44]).
- `fastdocparse list-schemas`, to browse the bundled example schemas ([#47]).
- A top-level `--version` flag ([#43]).

### Changed

- Running with no credentials configured fails immediately with setup
  instructions, instead of surfacing a raw OpenAI authentication error ([#44]).
- Omitting the schema argument to `extract` shows a next step rather than
  Typer's generic error ([#33], [#35]).

### Fixed

- `test_catastrophic_backtracking_pattern_does_not_hang` failed on Windows for a
  reason unrelated to the code under test: the ReDoS guard uses `SIGALRM`, which
  Windows does not have, so the guard does not run there at all. The limitation
  was already documented in `grounding.py`; the test now skips with that reason
  rather than failing red without one ([#28], [#45]).
- `TypeError` is raised for an invalid example type, instead of a less specific
  error ([#26]).

### Documentation

- Docstrings for `FieldResult`, `ExtractionMeta` and `ExtractionResult` ([#41]).
- A proofreading pass correcting stale facts across the README and docs ([#42]).
- The stale spec document was replaced by a build history, with the roadmap split
  out separately ([#48]), and the hybrid OCR+VLM entry expanded with its real
  challenges ([#49]) and a diagram ([#50]).
- Branding banner and tagline in the README ([#51], [#52], [#53]).

## [0.2.0] — 2026-08-29

### Changed

- **The package, the import name and the CLI are all `fastdocparse`.** 0.1.x
  shipped under the new distribution name while the code inside still said
  `docextract`, so `pip install fastdocparse` followed by `import fastdocparse`
  did not work. This is the release that made the rename real, and it is why it
  is a minor bump rather than a patch.

## [0.1.1] — 2026-08-29

### Fixed

- `__version__` looked up the wrong distribution name and could not resolve it
  after the PyPI rename.

## [0.1.0] — 2026-08-29

### Added

- First release: document field extraction with grounding-based confidence, so a
  result says where in the document each value came from.
- Installable `src/` package layout, with the test suite under `tests/`.
- Architecture diagrams and the initial documentation set.

[#26]: https://github.com/pranjalparmar/fastdocparse/pull/26
[#28]: https://github.com/pranjalparmar/fastdocparse/issues/28
[#33]: https://github.com/pranjalparmar/fastdocparse/issues/33
[#35]: https://github.com/pranjalparmar/fastdocparse/pull/35
[#41]: https://github.com/pranjalparmar/fastdocparse/pull/41
[#42]: https://github.com/pranjalparmar/fastdocparse/pull/42
[#43]: https://github.com/pranjalparmar/fastdocparse/pull/43
[#44]: https://github.com/pranjalparmar/fastdocparse/pull/44
[#45]: https://github.com/pranjalparmar/fastdocparse/pull/45
[#46]: https://github.com/pranjalparmar/fastdocparse/pull/46
[#47]: https://github.com/pranjalparmar/fastdocparse/pull/47
[#48]: https://github.com/pranjalparmar/fastdocparse/pull/48
[#49]: https://github.com/pranjalparmar/fastdocparse/pull/49
[#50]: https://github.com/pranjalparmar/fastdocparse/pull/50
[#51]: https://github.com/pranjalparmar/fastdocparse/pull/51
[#52]: https://github.com/pranjalparmar/fastdocparse/pull/52
[#53]: https://github.com/pranjalparmar/fastdocparse/pull/53
[#63]: https://github.com/pranjalparmar/fastdocparse/issues/63
[Unreleased]: https://github.com/pranjalparmar/fastdocparse/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/pranjalparmar/fastdocparse/releases/tag/v0.3.0
