from __future__ import annotations

import json
import re
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from anomalies import inject_anomalies
from config import PipelineConfig, parse_config
from detector import detect_anomalies
from evaluation import evaluate_detections
from main import _sql_value, _table_frames, run_pipeline
from master_data import generate_master_data, generate_opening_balances, generate_skus
from transactions import generate_transactions
from validate import ValidationFailure, validate_data
from verify_sql_seed_load import validate_sql_load


def small_config(output_dir: Path) -> PipelineConfig:
    return PipelineConfig(
        seed=20261022, scale="test", store_count=3, sku_count=8,
        employee_count=15, shift_count=5_475, promotion_count=8,
        movement_count=240, cycle_count_count=50, output_dir=output_dir,
    )


class PipelineTests(unittest.TestCase):
    def _assert_dictionary_schema_parity(self, frames: dict[str, pd.DataFrame]) -> None:
        project_root = Path(__file__).parents[1]
        dictionary = (project_root / "data_dictionary.md").read_text(encoding="utf-8")
        ddl = (project_root / "schema.sql").read_text(encoding="utf-8")
        dictionary_sections = {
            "stores": "Store", "skus": "SKU", "employees": "Employee (supporting actor table)",
            "employee_roles": "EmployeeRole", "shifts": "Shift", "promotions": "Promotion",
            "inventory_movements": "InventoryMovement", "cycle_counts": "CycleCount",
            "returns": "Return", "shrinkage_alerts": "ShrinkageAlert",
            "investigations": "Investigation", "opening_balances": "OpeningBalances",
            "ground_truth": "GroundTruth", "event_history": "EventHistory",
            "detections": "Detection", "evaluation_metrics": "EvaluationMetrics",
        }
        sql_tables = {
            "stores": "Store", "skus": "SKU", "employees": "Employee",
            "employee_roles": "EmployeeRole", "shifts": "Shift", "promotions": "Promotion",
            "opening_balances": "OpeningBalances", "inventory_movements": "InventoryMovement",
            "cycle_counts": "CycleCount", "returns": "ReturnEntity", "ground_truth": "GroundTruth",
            "event_history": "EventHistory", "detections": "Detection",
            "shrinkage_alerts": "ShrinkageAlert", "investigations": "Investigation",
            "evaluation_metrics": "EvaluationMetrics",
        }
        for export_name, frame in frames.items():
            heading = dictionary_sections[export_name]
            section_match = re.search(
                rf"^### {re.escape(heading)}\s*$(.*?)(?=^### |^## |\Z)",
                dictionary, re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(section_match, f"missing data dictionary section {heading}")
            documented = set(re.findall(r"^\| `([^`]+)` \|", section_match.group(1), re.MULTILINE))
            self.assertEqual(documented, set(frame.columns), f"dictionary fields for {export_name}")

            sql_table = sql_tables[export_name]
            table_match = re.search(
                rf"CREATE TABLE IF NOT EXISTS {re.escape(sql_table)}\s*\((.*?)\n\);",
                ddl, re.DOTALL,
            )
            self.assertIsNotNone(table_match, f"missing DDL table {sql_table}")
            schema_columns = set()
            for row in table_match.group(1).splitlines():
                row = row.strip().rstrip(",")
                if not row or row.startswith((
                    "PRIMARY KEY", "UNIQUE", "FOREIGN KEY", "REFERENCES", "CHECK", "OR", "AND"
                )):
                    continue
                schema_columns.add(row.split()[0].strip('"'))
            self.assertEqual(schema_columns, set(frame.columns), f"DDL fields for {export_name}")

    def test_cli_scale_seed_and_output_directory(self) -> None:
        config = parse_config(["--seed", "17", "--scale", "starter", "--output-dir", "tmp-out"])
        self.assertEqual(config.seed, 17)
        self.assertEqual(config.store_count, 10)
        self.assertEqual(config.sku_count, 100)
        self.assertEqual(config.movement_count, 10_000)
        self.assertEqual(config.cycle_count_count, 500)
        self.assertEqual(config.output_dir, Path("tmp-out"))

    def test_end_to_end_exports_and_reproducibility(self) -> None:
        with tempfile.TemporaryDirectory(prefix="inventory-test-") as temporary:
            config = small_config(Path(temporary) / "run")
            hashes = run_pipeline(config, verify_reproducibility=True)
            report = json.loads(config.validation_summary_path.read_text(encoding="utf-8"))
            self.assertEqual(report["overall_status"], "PASS")
            self.assertEqual(report["failed"], 0)
            self.assertEqual(len(hashes), 16 * 3)
            sql_counts = validate_sql_load(config.output_dir, Path(__file__).parents[1] / "schema.sql")
            self.assertEqual(len(sql_counts), 16)
            self.assertEqual(sql_counts["InventoryMovement"], config.movement_count)
            frames = _table_frames(*self._generated(config))
            self._assert_dictionary_schema_parity(frames)
            for name, frame in frames.items():
                csv_frame = pd.read_csv(config.output_dir / f"{name}.csv")
                json_frame = pd.read_json(config.output_dir / f"{name}.json", orient="records")
                self.assertEqual(len(csv_frame), len(frame), name)
                self.assertEqual(len(json_frame), len(frame), name)
                self.assertEqual(list(csv_frame.columns), list(frame.columns), name)
                self.assertEqual(list(json_frame.columns), list(frame.columns), name)
                expected_json = json.loads(frame.to_json(
                    orient="records", date_format="iso", date_unit="ns", force_ascii=False, indent=2
                ))
                actual_json = json.loads(
                    (config.output_dir / f"{name}.json").read_text(encoding="utf-8")
                )
                self.assertEqual(actual_json, expected_json, name)
                sql = (config.output_dir / f"{name}.sql").read_text(encoding="utf-8")
                self.assertTrue(sql.startswith("-- Seed data for "))
                if len(frame):
                    self.assertIn("INSERT INTO", sql)
            self.assertTrue(config.source_manifest_path.exists())
            self.assertTrue(config.hash_manifest_path.exists())

    def test_sql_null_serialization_preserves_nullable_values(self) -> None:
        self.assertEqual(_sql_value(""), "NULL")
        self.assertEqual(_sql_value(None), "NULL")
        self.assertEqual(_sql_value("reason"), "'reason'")

    def test_anomalies_detected_without_ground_truth_as_detector_input(self) -> None:
        with tempfile.TemporaryDirectory(prefix="inventory-test-data-") as temporary:
            config = small_config(Path(temporary))
            master = generate_master_data(config)
            transactions = generate_transactions(master, config)
            anomalous = inject_anomalies(master, transactions, config)
            detections = detect_anomalies(anomalous.movements, anomalous.cycle_counts, master.stores, config)
            truth = set(zip(anomalous.ground_truth.entity_type, anomalous.ground_truth.target_id,
                            anomalous.ground_truth.issue_type))
            predicted = set(zip(detections.entity_type, detections.target_id, detections.issue_type))
            self.assertEqual(predicted, truth)
            self.assertTrue(anomalous.detections.risk_score.between(0, 1).all())
            expected_score = (0.5 + 0.5 * anomalous.detections.loss_qty.astype(float)
                             .div(anomalous.detections.loss_qty.astype(float) + 10.0)).round(8)
            self.assertTrue((anomalous.detections.risk_score == expected_score).all())
            self.assertEqual(set(anomalous.ground_truth.issue_type), {
                "Wrong_Store_Transfer", "No_Reason_Adjustment", "Time_Travel_Count",
                "Double_Shrinkage_Transfer",
            })
            self.assertNotIn("issue_type", anomalous.movements.columns)
            self.assertEqual(set(anomalous.investigations.status), {"Open", "In_Progress", "Closed"})
            self.assertTrue(anomalous.event_history.event_type.isin({
                "ANOMALY_INJECTION", "STATUS_INITIALIZED", "STATUS_TRANSITION",
            }).all())

    def test_clean_baseline_has_no_anomaly_false_positives(self) -> None:
        with tempfile.TemporaryDirectory(prefix="inventory-test-clean-") as temporary:
            config = small_config(Path(temporary))
            master = generate_master_data(config)
            transactions = generate_transactions(master, config)
            detections = detect_anomalies(
                transactions.movements, transactions.cycle_counts, master.stores, config
            )
            self.assertTrue(detections.empty)
            self.assertEqual(
                set(transactions.returns.movement_id),
                set(transactions.movements.loc[
                    transactions.movements.movement_type.eq("Return"), "movement_id"
                ]),
            )

    def test_boundary_configuration_and_opening_quantity(self) -> None:
        config = replace(
            small_config(Path("unused")), start_date="2026-01-01", end_date="2026-01-01",
            shift_count=15, opening_qty_min=1, opening_qty_max=1,
        )
        master = generate_master_data(config)
        self.assertTrue(master.opening_balances.opening_qty.eq(1).all())
        self.assertTrue(master.shifts.shift_date.eq("2026-01-01").all())
        with self.assertRaises(ValueError):
            replace(config, movement_count=100_000_000)
        with self.assertRaises(ValueError):
            replace(config, cycle_count_count=1_000_000)

    def test_category_distributions_are_deterministic_and_category_aware(self) -> None:
        config = PipelineConfig(
            seed=20261022, scale="distribution-test", store_count=2, sku_count=600,
            employee_count=10, shift_count=3_650, promotion_count=10,
            movement_count=1_200, cycle_count_count=10,
        )
        skus = generate_skus(config)
        repeated = generate_skus(config)
        pd.testing.assert_frame_equal(skus, repeated, check_exact=True)
        medians = skus.groupby("category").unit_cost.median()
        self.assertGreater(medians["Electronics"], medians["Grocery"] * 5)
        balances = generate_opening_balances(config, skus)
        balances = balances.merge(skus[["sku_id", "category"]], on="sku_id")
        stock_medians = balances.groupby("category").opening_qty.median()
        self.assertGreater(stock_medians["Grocery"], stock_medians["Electronics"])

    def test_two_store_wrong_destination_is_existing_and_mismatched(self) -> None:
        with tempfile.TemporaryDirectory(prefix="inventory-test-two-store-") as temporary:
            config = PipelineConfig(
                seed=20261022, scale="test", store_count=2, sku_count=12,
                employee_count=10, shift_count=18_250, promotion_count=3,
                movement_count=240, cycle_count_count=4, output_dir=Path(temporary),
            )
            master = generate_master_data(config)
            transactions = generate_transactions(master, config)
            anomalous = inject_anomalies(master, transactions, config)
            target = anomalous.ground_truth.loc[
                anomalous.ground_truth.issue_type.eq("Wrong_Store_Transfer")
            ].iloc[0]
            outbound = anomalous.movements.set_index("movement_id").loc[target.target_id]
            inbound = anomalous.movements.loc[
                anomalous.movements.transfer_id.eq(outbound.transfer_id)
                & anomalous.movements.movement_type.eq("Transfer_In")
            ].iloc[0]
            self.assertIn(outbound.related_store_id, set(master.stores.store_id))
            self.assertNotEqual(outbound.related_store_id, inbound.store_id)
            self.assertEqual(outbound.store_id, inbound.related_store_id)

    def test_critical_validation_failure_stops_exports(self) -> None:
        with tempfile.TemporaryDirectory(prefix="inventory-test-failure-") as temporary:
            config = small_config(Path(temporary))
            master = generate_master_data(config)
            transactions = generate_transactions(master, config)
            anomalous = inject_anomalies(master, transactions, config)
            broken = replace(master, stores=pd.concat([master.stores, master.stores.iloc[[0]]], ignore_index=True))
            with patch("main._generate", return_value=(broken, transactions, anomalous)):
                with self.assertRaises(ValidationFailure):
                    run_pipeline(config, verify_reproducibility=False)
            report = json.loads(config.validation_summary_path.read_text(encoding="utf-8"))
            self.assertEqual(report["overall_status"], "FAIL")
            self.assertFalse((config.output_dir / "stores.csv").exists())

    def test_master_relationships_and_baseline_stock_never_negative(self) -> None:
        with tempfile.TemporaryDirectory(prefix="inventory-test-data-") as temporary:
            config = small_config(Path(temporary))
            master = generate_master_data(config)
            transactions = generate_transactions(master, config)
            self.assertEqual(len(master.employee_roles), len(master.employees))
            self.assertEqual(len(master.opening_balances), config.store_count * config.sku_count)
            ledger = transactions.movements.sort_values(["store_id", "sku_id", "timestamp", "movement_id"])
            workload = ledger.groupby(["store_id", "sku_id"]).size()
            self.assertGreater(workload.nunique(), 1, "event workload should vary across Store/SKU pairs")
            ledger["running"] = ledger.groupby(["store_id", "sku_id"]).qty_change.cumsum()
            opening = master.opening_balances.set_index(["store_id", "sku_id"]).opening_qty
            keys = pd.MultiIndex.from_arrays([ledger.store_id, ledger.sku_id])
            self.assertTrue((ledger.running.to_numpy() + opening.reindex(keys).to_numpy() >= 0).all())
            self.assertTrue(transactions.movements.movement_id.str.fullmatch(r"MOV-2026-\d{8}").all())
            self.assertTrue(transactions.cycle_counts.cycle_count_id.str.fullmatch(r"CNT-2026-\d{6}").all())

    def test_metric_zero_denominators_are_safe(self) -> None:
        ground_truth = pd.DataFrame(columns=["entity_type", "target_id", "issue_type"])
        detections = pd.DataFrame(columns=["entity_type", "target_id", "issue_type"])
        movements = pd.DataFrame(columns=["movement_type", "qty_change"])
        counts = pd.DataFrame(columns=["cycle_count_id"])
        metrics = evaluate_detections(ground_truth, detections, movements, counts)
        self.assertTrue((metrics[["precision", "recall", "f1_score"]] == 0).all().all())

    def test_schema_documents_core_and_supporting_tables(self) -> None:
        schema = (Path(__file__).resolve().parents[1] / "schema.sql").read_text(encoding="utf-8")
        for table in ("Store", "SKU", "InventoryMovement", "CycleCount", "EmployeeRole",
                      "Shift", "Promotion", "ReturnEntity", "ShrinkageAlert", "Investigation"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table} (", schema)
        self.assertIn("^CNT-2026-[0-9]{6}$", schema)
        self.assertIn("risk_score NUMERIC(9,8)", schema)
        self.assertIn("REFERENCES EmployeeRole(employee_role_id, employee_id)", schema)
        self.assertIn("CREATE TABLE IF NOT EXISTS EventHistory", schema)

    def _generated(self, config: PipelineConfig):
        master = generate_master_data(config)
        transactions = generate_transactions(master, config)
        return master, inject_anomalies(master, transactions, config)


if __name__ == "__main__":
    unittest.main()
