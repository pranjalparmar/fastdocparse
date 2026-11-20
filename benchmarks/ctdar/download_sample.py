#!/usr/bin/env python3
"""Download a reproducible cTDaR Track B1 sample outside the repository."""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from urllib.request import Request, urlopen


TREE_URL = "https://api.github.com/repos/cndplab-founder/ICDAR2019_cTDaR/git/trees/master?recursive=1"
RAW_BASE = "https://raw.githubusercontent.com/cndplab-founder/ICDAR2019_cTDaR/master/"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def _fetch(url: str, timeout: int = 120) -> bytes:
    request = Request(url, headers={"User-Agent": "fastdocparse-benchmark/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def download(output: Path, limit: int = 30, seed: int = 2019) -> list[str]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    tree = json.loads(_fetch(TREE_URL, timeout=60))
    base = "training/TRACKB1/ground_truth/"
    paths = [entry["path"] for entry in tree.get("tree", []) if entry.get("type") == "blob" and entry["path"].startswith(base)]
    xml_stems = {Path(path).stem for path in paths if Path(path).suffix.lower() == ".xml"}
    image_by_stem = {Path(path).stem: path for path in paths if Path(path).suffix.lower() in IMAGE_EXTENSIONS}
    available = sorted(xml_stems & image_by_stem.keys())
    if not available:
        raise RuntimeError("No paired cTDaR Track B1 images and XML annotations found")
    selected = available if limit >= len(available) else sorted(random.Random(seed).sample(available, limit))
    target = output / "training" / "TRACKB1" / "ground_truth"
    target.mkdir(parents=True, exist_ok=True)
    for index, stem in enumerate(selected, 1):
        for source_path in (f"{base}{stem}.xml", image_by_stem[stem]):
            destination = target / Path(source_path).name
            if not destination.exists():
                destination.write_bytes(_fetch(RAW_BASE + source_path))
        if index % 10 == 0 or index == len(selected):
            print(f"Downloaded {index}/{len(selected)} cTDaR Track B1 sample documents")
        time.sleep(0.05)
    (output / "sample_manifest.json").write_text(
        json.dumps({"repository": "cndplab-founder/ICDAR2019_cTDaR", "subset": "training/TRACKB1", "seed": seed, "documents": selected}, indent=2) + "\n",
        encoding="utf-8",
    )
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--seed", type=int, default=2019)
    args = parser.parse_args()
    selected = download(args.output, args.limit, args.seed)
    print(f"Downloaded {len(selected)} cTDaR samples to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
