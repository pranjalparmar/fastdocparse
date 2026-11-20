# cTDaR scanned-table baseline

This benchmark evaluates the parser's current scanned-image output against cTDaR
table regions and cells. The cTDaR repository has Track A table detection
annotations and Track B1 table-region/cell annotations. The current parser does
not emit table regions or cells for image-only scanned pages, so a zero score is
the honest current baseline. The benchmark does not train, download, or invoke
an alternate model.

The reproducible default sample selects 30 documents from the public Track B1
annotation repository using seed 2019. It is a **sample of training/development
data**, not a held-out evaluation split. Images and XML are downloaded to an
explicit path outside the repository; do not commit the data. The repository
does not expose a clear dataset license in its README. Check with dataset
organizers before redistribution or non-research use.

Reported diagnostics:

- Table detection: one-to-one matching of axis-aligned envelopes at IoU >= 0.50
  as a diagnostic, plus polygon-IoU precision/recall/F1 at cTDaR thresholds
  0.6/0.7/0.8/0.9.
- Cell geometry: one-to-one matching of cell axis-aligned envelopes at IoU >=
  0.50 as a diagnostic.
- Grid spans: exact start/end row and column span agreement among spatially
  matched cells, when the parser emits such attributes.
- Track B1 adjacency relations: relation precision and recall at each cell IoU
  threshold, reproducing the public evaluator's table match IoU of 0.8 and
  directed horizontal/vertical nearest-neighbor relation definition.

cTDaR cell and table annotations are polygons. The envelope measures are only
diagnostics; the additional Track A and Track B1 polygon/relation counters
follow the public evaluator logic on parser outputs. The adjacency relation
sets were cross-checked against the evaluator on all 35 tables in this sample.
Track B1 supplies a
table region, while this baseline passes the full scan to the unchanged parser;
no ground-truth region crop is provided. Treat results as a zero baseline of
parser capability, not a comparison with cTDaR leaderboard systems. Shapely is
required for polygon IoU (`pip install -r benchmarks/ctdar/requirements.txt`).

## Download and run

```sh
python3 benchmarks/ctdar/download_sample.py \
  --output /path/outside/repo/ctdar-sample --limit 30 --seed 2019
./venv/bin/python benchmarks/ctdar/benchmark_tables.py \
  --data-dir /path/outside/repo/ctdar-sample \
  --subset training/TRACKB1 --output reports/ctdar-trackb1-baseline.json
```

Use `--subset training/TRACKA` for table-region-only labels, if that subset is
downloaded in the expected layout. Sample IDs are saved in
`sample_manifest.json`.

## Source

- Official site: <https://cndplab-founder.github.io/cTDaR2019/>
- Dataset repository: <https://github.com/cndplab-founder/ICDAR2019_cTDaR>
- Evaluation description/tool: <https://github.com/cndplab-founder/ctdar_measurement_tool>
