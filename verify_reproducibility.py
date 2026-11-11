from __future__ import annotations

import tempfile
import logging
from pathlib import Path

from config import PipelineConfig
from main import run_pipeline
from validate import compare_hash_manifests, sha256_file


DEFAULT_SEED = 20261022
ARTIFACT_SUFFIXES = {".csv", ".json", ".sql"}


def _artifact_hashes(output_dir: Path) -> dict[str, str]:
    artifacts = sorted(
        path for path in output_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in ARTIFACT_SUFFIXES
    )
    if not artifacts:
        raise RuntimeError(f"No CSV, JSON, or SQL artifacts found in {output_dir}")
    return {path.relative_to(output_dir).as_posix(): sha256_file(path) for path in artifacts}


def _csv_row_counts(output_dir: Path) -> dict[str, int]:
    counts = {}
    for path in sorted(output_dir.glob("*.csv")):
        if path.name == "sha256_manifest.csv":
            continue
        with path.open("r", encoding="utf-8", newline="") as stream:
            counts[path.name] = max(0, sum(1 for _ in stream) - 1)
    return counts


def main() -> int:
    logging.getLogger("inventory_pipeline").setLevel(logging.WARNING)
    with tempfile.TemporaryDirectory(prefix="inventory-verify-run-1-") as run_1_dir, \
         tempfile.TemporaryDirectory(prefix="inventory-verify-run-2-") as run_2_dir:
        config_1 = PipelineConfig(seed=DEFAULT_SEED, scale="full", output_dir=Path(run_1_dir))
        config_2 = PipelineConfig(seed=DEFAULT_SEED, scale="full", output_dir=Path(run_2_dir))

        print(f"Generating Run 1 with seed {DEFAULT_SEED} (full scale)...")
        run_pipeline(config_1, verify_reproducibility=False)
        hashes_1 = _artifact_hashes(Path(run_1_dir))

        print(f"Generating Run 2 with seed {DEFAULT_SEED} (full scale)...")
        run_pipeline(config_2, verify_reproducibility=False)
        hashes_2 = _artifact_hashes(Path(run_2_dir))
        counts_1 = _csv_row_counts(Path(run_1_dir))
        counts_2 = _csv_row_counts(Path(run_2_dir))

        compare_hash_manifests(hashes_1, hashes_2)
        if counts_1 != counts_2:
            raise AssertionError(f"CSV row counts differ between runs: {counts_1} != {counts_2}")
        print("\nREPRODUCIBILITY VERIFICATION: PASS")
        print("All generated CSV, JSON, and SQL files are byte-for-byte identical.")
        print(f"Seed: {DEFAULT_SEED}")
        print(f"Artifacts compared: {len(hashes_1)}")
        print(f"CSV tables and row counts compared: {len(counts_1)}")
        print("SHA-256 comparison: 100% match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
