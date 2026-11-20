# FUNSD OCR baseline benchmark

This benchmark measures the current `fastdocparse` scanned-PDF OCR path against
the official FUNSD test split. FUNSD contains 199 scanned forms; the official
split has 149 training and 50 test pages. Its annotations provide word text and
boxes grouped into form entities. This benchmark spatially groups those words
into approximate text lines so they can be compared with RapidOCR's line-level
detections.

## Scope

The first run measures **OCR text and line localization**, not complete layout
reconstruction. FUNSD does not provide generic page-region or table-grid labels,
and the current parser does not predict FUNSD's question/answer entity classes.
Those tasks need separate datasets and compatible predictions.

Reported metrics:

- Line detection precision, recall, and F1 at IoU 0.5 and 0.75, with one-to-one
  spatial matching.
- Character error rate (CER) for spatially matched lines; unmatched reference
  and predicted lines count as deletions and insertions.
- Document count, reference/predicted line counts, and per-document metrics.

Ground-truth word boxes are merged into approximate lines by vertical overlap.
That conversion is a benchmark convention, not an official FUNSD line annotation.
Results should therefore be read as a baseline and error diagnostic, not as an
official FUNSD leaderboard score.

## Get the data

Download the official archive from <https://guillaumejaume.github.io/FUNSD/> and
extract it outside the repository. The official archive SHA-256 is
`c31735649e4f441bcbb4fd0f379574f7520b42286e80b01d80b445649d54761f`.
FUNSD's license is for non-commercial research and educational use; review the
dataset's current license before use. Do not commit the dataset or its images to
this repository.

## Run

From the repository root, with the project environment installed:

```sh
./venv/bin/python benchmarks/funsd/benchmark_ocr.py \
  --data-dir /path/to/dataset \
  --split testing_data \
  --output /path/to/funsd-test-results.json
```

`--data-dir` should contain `training_data/` and `testing_data/`, each with
`images/` and `annotations/`. Use `--split training_data` only for exploratory
development; report test results from `testing_data` and do not tune against it.
No extra benchmark packages are required beyond the project runtime.

The benchmark wraps each source image in a one-page PDF and invokes
`parse_pdf_document`, so it exercises the parser's actual image-only PDF route.
The JSON output records metric definitions and per-document results for
reproducibility.

## Source

- Jaume, G., Ekenel, H. K., and Thiran, J.-P. “FUNSD: A Dataset for Form
  Understanding in Noisy Scanned Documents.” ICDARW, 2019.
  <https://arxiv.org/abs/1905.13538>
- Official dataset and license: <https://guillaumejaume.github.io/FUNSD/>
