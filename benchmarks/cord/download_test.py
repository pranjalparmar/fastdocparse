#!/usr/bin/env python3
"""Download a public CORD v2 split from Hugging Face or a Parquet shard."""

from __future__ import annotations

import argparse
import io
import json
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API = "https://datasets-server.huggingface.co/rows"


def _get_json(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "fastdocparse-benchmark/1.0"})
    return _open_with_retries(request, timeout=60, json_response=True)


def _open_with_retries(request: Request, timeout: int, json_response: bool = False) -> bytes | dict:
    last_error = None
    for attempt in range(5):
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.load(response) if json_response else response.read()
        except Exception as error:
            last_error = error
            if attempt == 4:
                break
            time.sleep(0.5 * (2**attempt))
    assert last_error is not None
    raise last_error


def download(output: Path, split: str = "test", limit: int | None = None) -> int:
    output.mkdir(parents=True, exist_ok=True)
    images_dir, annotations_dir = output / "images", output / "annotations"
    images_dir.mkdir(exist_ok=True)
    annotations_dir.mkdir(exist_ok=True)
    first = _get_json(API + "?" + urlencode({"dataset": "naver-clova-ix/cord-v2", "config": "default", "split": split, "offset": 0, "length": 1}))
    total = first["num_rows_total"]
    count = min(total, limit) if limit is not None else total

    for offset in range(0, count, 10):
        length = min(10, count - offset)
        page = _get_json(API + "?" + urlencode({"dataset": "naver-clova-ix/cord-v2", "config": "default", "split": split, "offset": offset, "length": length}))
        for row_info in page["rows"]:
            index = row_info["row_idx"]
            row = row_info["row"]
            image_url = row["image"].get("src")
            if not image_url:
                raise ValueError(f"CORD row {index} has no image URL")
            annotation = json.loads(row["ground_truth"])
            image_path = images_dir / f"{index:04d}.jpg"
            annotation_path = annotations_dir / f"{index:04d}.json"
            if not image_path.exists():
                request = Request(image_url, headers={"User-Agent": "fastdocparse-benchmark/1.0"})
                image_path.write_bytes(_open_with_retries(request, timeout=120))
            annotation_path.write_text(json.dumps(annotation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Downloaded {min(offset + length, count)}/{count} CORD {split} receipts")
        # Avoid bursty requests to the dataset viewer and cached image endpoint.
        if offset + length < count:
            time.sleep(0.5)
    return count


def download_parquet(parquet_path: Path, output: Path, split: str = "validation", limit: int | None = None) -> int:
    """Extract a CORD split from its public Hugging Face Parquet shard.

    This is a fallback for datasets-server outages. It requires optional pyarrow
    and writes only the requested split's images/annotations outside the repo.
    """
    if split not in {"train", "validation", "test"}:
        raise ValueError(f"Unsupported CORD split: {split}")
    try:
        import pyarrow.parquet as parquet
    except ImportError as error:
        raise RuntimeError("Parquet fallback requires pyarrow (pip install pyarrow)") from error

    output.mkdir(parents=True, exist_ok=True)
    images_dir, annotations_dir = output / "images", output / "annotations"
    images_dir.mkdir(exist_ok=True)
    annotations_dir.mkdir(exist_ok=True)
    parquet_file = parquet.ParquetFile(parquet_path)
    count = 0
    for batch in parquet_file.iter_batches(batch_size=16, columns=["image", "ground_truth"]):
        for row in batch.to_pylist():
            annotation = json.loads(row["ground_truth"])
            # Dataset rows expose the official split in annotation metadata.
            row_split = annotation.get("meta", {}).get("split")
            if row_split == "valid":
                row_split = "validation"
            if row_split != split:
                continue
            row_index = annotation.get("meta", {}).get("image_id", count)
            stem = f"{int(row_index):04d}"
            image_bytes = row["image"].get("bytes")
            if image_bytes is None:
                image_file = row["image"].get("path")
                if not image_file:
                    raise ValueError(f"CORD Parquet row {row_index} has neither embedded image bytes nor a path")
                image_bytes = Path(image_file).read_bytes()
            if not any((images_dir / f"{stem}{suffix}").exists() for suffix in (".jpg", ".jpeg", ".png")):
                from PIL import Image

                with Image.open(io.BytesIO(image_bytes)) as image:
                    image_format = (image.format or "JPEG").lower()
                    suffix = ".png" if image_format == "png" else ".jpg"
                (images_dir / f"{stem}{suffix}").write_bytes(image_bytes)
            (annotations_dir / f"{stem}.json").write_text(
                json.dumps(annotation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            count += 1
            if limit is not None and count >= limit:
                return count
    if count == 0:
        raise ValueError(f"No {split} rows found in {parquet_path}")
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Output directory; images and annotations are written here")
    parser.add_argument("--split", choices=("test", "validation", "train"), default="test")
    parser.add_argument("--limit", type=int, help="Download only the first N rows (smoke tests)")
    parser.add_argument("--parquet", type=Path, help="Use an already downloaded HF Parquet shard instead of the viewer API")
    args = parser.parse_args()
    if args.parquet:
        count = download_parquet(args.parquet, args.output, args.split, args.limit)
    else:
        count = download(args.output, args.split, args.limit)
    print(f"Downloaded {count} CORD receipts to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
