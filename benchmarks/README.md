# Benchmarks

These scripts measure current fastdocparse behavior against public annotated
document datasets. Dataset files are downloaded to explicit external paths and
are not committed here.

| Benchmark | Task | Dataset / split | Instructions |
| --- | --- | --- | --- |
| FUNSD | Scanned form OCR and line localization | FUNSD official test, 50 pages | [README](funsd/README.md) |
| CORD v2 | Receipt OCR and line localization/grouping coverage | Hugging Face `naver-clova-ix/cord-v2`, test, 100 receipts | [README](cord/README.md) |
| cTDaR | Scanned table detection and cell-geometry capability baseline | Seeded 30-doc sample from public Track B1 training annotations | [README](ctdar/README.md) |

Each report separates task metrics and records dataset split and annotation
caveats. Scores are diagnostic baselines, not claims of general performance.
See [the combined baseline report](../reports/Scanned%20structure%20and%20receipt%20baselines.md).
