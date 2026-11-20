# CORD v2 receipt OCR and grouping baseline

This benchmark uses the 100-row `test` split from Hugging Face dataset
`naver-clova-ix/cord-v2`. CORD v2 is released under CC BY 4.0. The benchmark
downloads the test images and `ground_truth` annotations to a caller-selected
directory outside the repository; it does not add dataset images to git.

CORD's `valid_line` annotations include line text, word quadrilaterals, and
`group_id`/`row_id` labels. The runner measures positioned OCR text/line
localization and CER. OCR output is line-grouped by the parser; grouping recall
is reported as the number of annotated lines whose envelope is localized at
IoU >= 0.50. Semantic `group_id` and menu `row_id` prediction is not currently
implemented and is explicitly not scored. The structured `gt_parse` receipt
fields are also not scored as key-value extraction.

The comparison converts word quadrilaterals to axis-aligned bounding boxes. CORD
line text is formed by ordering words by the left edge of their quadrilaterals.
Line matching is maximum-cardinality, one-to-one IoU matching. CER uses
whitespace-normalized text for spatially matched lines; unmatched reference and
predicted lines count as deletions and insertions. This is a project diagnostic,
not a claimed official CORD leaderboard metric.

## Download and run

From the repository root:

```sh
python3 benchmarks/cord/download_test.py \
  --output /path/outside/repo/cord-v2-test --split test
./venv/bin/python benchmarks/cord/benchmark_receipts.py \
  --data-dir /path/outside/repo/cord-v2-test --split test \
  --output reports/cord-v2-test-baseline.json
```

The downloader uses the Hugging Face dataset viewer API and resumes already
downloaded image files. Use `--limit N` for a smoke test. It exports
`images/` and `annotations/` to the destination.

The viewer API may be unavailable. As a fallback, download the official split
Parquet shard from the dataset repository and extract it without checking the
shard into the repo (requires `pyarrow`):

```sh
./venv/bin/python benchmarks/cord/download_test.py \
  --parquet /path/outside/repo/cord-v2-validation.parquet \
  --output /path/outside/repo/cord-v2-validation --split validation
```

For the validation-only post-hoc threshold exploration (the runner refuses the
test split):

```sh
./venv/bin/python benchmarks/cord/benchmark_grouping_sweep.py \
  --data-dir /path/outside/repo/cord-v2-validation \
  --output reports/cord-v2-validation-grouping-sweep.json
```

The sweep changes only grouping in benchmark code; it does not modify parser
behavior. Evaluate changes on validation and freeze a setting before any final
test evaluation.

## Dataset information

- Dataset: <https://huggingface.co/datasets/naver-clova-ix/cord-v2>
- Dataset card/API: <https://huggingface.co/api/datasets/naver-clova-ix/cord-v2>
- License: CC BY 4.0 (attribute NAVER CLOVA and comply with the dataset card).
- The benchmark reports only the 100-record `test` split, not its train or
  validation records.
