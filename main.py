from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import replace
from datetime import date, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from anomalies import AnomalousData, inject_anomalies
from config import DEFAULT_CONFIG, PipelineConfig, parse_config
from master_data import MasterData, generate_master_data
from transactions import TransactionData, generate_transactions
from validate import ValidationFailure, compare_hash_manifests, sha256_file, validate_data


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("inventory_pipeline")


def _generate(config: PipelineConfig) -> tuple[MasterData, TransactionData, AnomalousData]:
    master = generate_master_data(config)
    transactions = generate_transactions(master, config)
    anomalous = inject_anomalies(master, transactions, config)
    return master, transactions, anomalous


def _sql_value(value: Any) -> str:
    if value is None or value is pd.NaT or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return "NULL"
    # Pandas uses empty strings for nullable CSV-export fields in some frames.
    # Preserve SQL NULL semantics so nullable foreign keys do not become
    # invalid empty-string references during seed loading.
    if isinstance(value, str) and value == "":
        return "NULL"
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, (pd.Timestamp, datetime, date)):
        value = value.isoformat(sep=" ") if isinstance(value, (pd.Timestamp, datetime)) else value.isoformat()
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def _write_sql(frame: pd.DataFrame, table_name: str, path: Path, batch_size: int = 1000) -> None:
    """Write deterministic, batched INSERT statements without materializing SQL in memory."""
    columns = list(frame.columns)
    quoted_columns = ", ".join(f'"{column.replace(chr(34), chr(34) * 2)}"' for column in columns)
    quoted_table = f'"{table_name}"'
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(f"-- Seed data for {table_name}\n")
        for start in range(0, len(frame), batch_size):
            chunk = frame.iloc[start : start + batch_size]
            values = []
            for row in chunk.itertuples(index=False, name=None):
                values.append("(" + ", ".join(_sql_value(value) for value in row) + ")")
            if values:
                stream.write(
                    f"INSERT INTO {quoted_table} ({quoted_columns}) VALUES\n  "
                    + ",\n  ".join(values)
                    + ";\n"
                )


def _serialize_tables(
    config: PipelineConfig, tables: dict[str, pd.DataFrame]
) -> dict[str, Path]:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    output_paths: dict[str, Path] = {}
    sql_names = {
        "stores": "Store", "skus": "SKU", "employees": "Employee",
        "employee_roles": "EmployeeRole", "shifts": "Shift", "promotions": "Promotion",
        "opening_balances": "OpeningBalances", "inventory_movements": "InventoryMovement",
        "cycle_counts": "CycleCount", "returns": "ReturnEntity",
        "ground_truth": "GroundTruth", "event_history": "EventHistory",
        "detections": "Detection", "shrinkage_alerts": "ShrinkageAlert",
        "investigations": "Investigation", "evaluation_metrics": "EvaluationMetrics",
    }
    for name, frame in tables.items():
        csv_path = config.output_dir / f"{name}.csv"
        json_path = config.output_dir / f"{name}.json"
        sql_path = config.output_dir / f"{name}.sql"
        frame.to_csv(csv_path, index=False, lineterminator="\n", date_format="%Y-%m-%dT%H:%M:%S.%f")
        json_text = frame.to_json(
            orient="records", date_format="iso", date_unit="ns", force_ascii=False, indent=2
        )
        json_path.write_text(json_text + "\n", encoding="utf-8")
        _write_sql(frame, sql_names[name], sql_path)
        output_paths.update({f"{name}.csv": csv_path, f"{name}.json": json_path, f"{name}.sql": sql_path})
        LOGGER.info("%s: %s rows exported as CSV, JSON, and SQL", name, f"{len(frame):,}")
    return output_paths


def _table_frames(master: MasterData, anomalous: AnomalousData) -> dict[str, pd.DataFrame]:
    return {
        "stores": master.stores,
        "skus": master.skus,
        "employees": master.employees,
        "employee_roles": master.employee_roles,
        "shifts": master.shifts,
        "promotions": master.promotions,
        "opening_balances": master.opening_balances,
        "inventory_movements": anomalous.movements,
        "cycle_counts": anomalous.cycle_counts,
        "returns": anomalous.returns,
        "detections": anomalous.detections,
        "shrinkage_alerts": anomalous.shrinkage_alerts,
        "investigations": anomalous.investigations,
        "event_history": anomalous.event_history,
        "ground_truth": anomalous.ground_truth,
        "evaluation_metrics": anomalous.evaluation_metrics,
    }


def _write_source_manifest(config: PipelineConfig) -> None:
    dependencies = {}
    for package in ("pandas", "numpy", "Faker"):
        try:
            dependencies[package] = version(package)
        except PackageNotFoundError:
            dependencies[package] = "not-installed"
    manifest = {
        "source_mode": "synthetic_only",
        "external_sources": [],
        "seed": config.seed,
        "scale": config.scale,
        "volumes": {"stores": config.store_count, "skus": config.sku_count,
                    "employees": config.employee_count, "shifts": config.shift_count,
                    "promotions": config.promotion_count, "movements": config.movement_count,
                    "cycle_counts": config.cycle_count_count},
        "date_range": {"start": config.start_date, "end": config.end_date},
        "generator": "Inventory Loss Detection synthetic pipeline",
        "dependencies": dependencies,
        "lawful_use": "Generated fictional records only; no external dataset is ingested.",
    }
    config.source_manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_pipeline(
    config: PipelineConfig = DEFAULT_CONFIG, *, verify_reproducibility: bool = True
) -> dict[str, str]:
    master, transactions, anomalous = _generate(config)
    summary = validate_data(master, transactions, anomalous, config)
    if summary["overall_status"] != "PASS":
        failures = [name for name, result in summary["checks"].items() if result["status"] == "FAIL"]
        raise ValidationFailure(f"Critical validation failed; exports withheld. See {config.validation_summary_path}: {failures}")
    _write_source_manifest(config)
    tables = _table_frames(master, anomalous)
    output_paths = _serialize_tables(config, tables)

    hashes = {name: sha256_file(path) for name, path in output_paths.items()}
    if verify_reproducibility:
        with tempfile.TemporaryDirectory(prefix="inventory-repro-") as temporary_dir:
            second_config = replace(config, output_dir=Path(temporary_dir))
            second_master, _, second_anomalous = _generate(second_config)
            _write_source_manifest(second_config)
            second_paths = _serialize_tables(second_config, _table_frames(second_master, second_anomalous))
            second_hashes = {name: sha256_file(path) for name, path in second_paths.items()}
            compare_hash_manifests(hashes, second_hashes)
            LOGGER.info("PASS byte-for-byte reproducibility across two generated runs")

    config.hash_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"file": name, "sha256": digest} for name, digest in hashes.items()]).to_csv(
        config.hash_manifest_path, index=False, lineterminator="\n"
    )
    LOGGER.info("Validation status: %s (%d passed, %d failed)",
                summary["overall_status"], summary["passed"], summary["failed"])
    for check_name, result in summary["checks"].items():
        LOGGER.info("%s %s: %s", result["status"], check_name, result["message"])
    LOGGER.info("Validation report: %s", config.validation_summary_path)
    LOGGER.info("Anomaly breakdown: %s", anomalous.ground_truth.issue_type.value_counts().to_dict())
    return hashes


if __name__ == "__main__":
    run_pipeline(parse_config())
