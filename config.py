from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class PipelineConfig:
    """Single source of truth for deterministic generation and output paths."""

    seed: int = 20261022
    scale: str = "full"
    store_count: int = 100
    sku_count: int = 5_000
    employee_count: int = 1_000
    # Store/day/role coverage yields 182,500 full and 18,250 starter shifts.
    shift_count: int = 182_500
    promotion_count: int = 500
    movement_count: int = 1_000_000
    cycle_count_count: int = 20_000
    start_date: str = "2026-01-01"
    end_date: str = "2026-12-31"
    opening_qty_min: int = 20
    opening_qty_max: int = 500
    output_dir: Path = Path("output")

    def __post_init__(self) -> None:
        counts = (self.store_count, self.sku_count, self.employee_count,
                  self.movement_count, self.cycle_count_count)
        if any(value <= 0 for value in counts):
            raise ValueError("All configured entity and event counts must be positive")
        if self.store_count < 2:
            raise ValueError("At least two stores are needed for transfer scenarios")
        if self.employee_count < self.store_count * 5:
            raise ValueError("employee_count must provide one employee per store/role combination")
        if self.movement_count > 99_999_999:
            raise ValueError("movement_count exceeds the eight-digit movement ID capacity")
        if self.movement_count < self.store_count * self.sku_count:
            raise ValueError("movement_count must cover every configured store/SKU opening balance")
        if self.cycle_count_count > 999_999:
            raise ValueError("cycle_count_count exceeds the six-digit count ID capacity")
        if date.fromisoformat(self.end_date) < date.fromisoformat(self.start_date):
            raise ValueError("end_date must not precede start_date")
        if self.opening_qty_min < 1 or self.opening_qty_max < self.opening_qty_min:
            raise ValueError("opening quantity bounds must be positive and ordered")

    @classmethod
    def from_cli(cls, args: argparse.Namespace) -> "PipelineConfig":
        config = cls(seed=args.seed, output_dir=Path(args.output_dir), scale=args.scale)
        if args.scale == "starter":
            config = replace(
                config, store_count=10, sku_count=100, employee_count=100,
                shift_count=18_250, promotion_count=50, movement_count=10_000,
                cycle_count_count=500,
            )
        return config

    @property
    def stores_path(self) -> Path:
        return self.output_dir / "stores.csv"

    @property
    def skus_path(self) -> Path:
        return self.output_dir / "skus.csv"

    @property
    def employees_path(self) -> Path:
        return self.output_dir / "employees.csv"

    @property
    def shifts_path(self) -> Path:
        return self.output_dir / "shifts.csv"

    @property
    def promotions_path(self) -> Path:
        return self.output_dir / "promotions.csv"

    @property
    def opening_balances_path(self) -> Path:
        return self.output_dir / "opening_balances.csv"

    @property
    def movements_path(self) -> Path:
        return self.output_dir / "inventory_movements.csv"

    @property
    def cycle_counts_path(self) -> Path:
        return self.output_dir / "cycle_counts.csv"

    @property
    def shrinkage_alerts_path(self) -> Path:
        return self.output_dir / "shrinkage_alerts.csv"

    @property
    def investigations_path(self) -> Path:
        return self.output_dir / "investigations.csv"

    @property
    def ground_truth_path(self) -> Path:
        return self.output_dir / "ground_truth.csv"

    @property
    def hash_manifest_path(self) -> Path:
        return self.output_dir / "sha256_manifest.csv"

    @property
    def validation_summary_path(self) -> Path:
        return self.output_dir / "validation_summary.json"

    @property
    def source_manifest_path(self) -> Path:
        return self.output_dir / "source_manifest.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate synthetic inventory loss data.")
    parser.add_argument("--seed", type=int, default=20261022, help="Fixed random seed.")
    parser.add_argument("--scale", choices=("starter", "full"), default="full")
    parser.add_argument("--output-dir", default="output", help="Output directory.")
    return parser


def parse_config(argv: list[str] | None = None) -> PipelineConfig:
    """Parse CLI options into a config; standard PipelineConfig() stays supported."""
    return PipelineConfig.from_cli(build_parser().parse_args(argv))


DEFAULT_CONFIG = PipelineConfig()
