from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pandas as pd

from anomalies import AnomalousData
from config import DEFAULT_CONFIG, PipelineConfig
from evaluation import evaluate_detections
from master_data import MasterData
from transactions import TransactionData


class ValidationFailure(RuntimeError):
    """Raised by the orchestrator when one or more critical checks fail."""


def _run_check(checks: dict[str, dict[str, object]], name: str, fn) -> None:
    try:
        fn()
        checks[name] = {"status": "PASS", "message": "Validation passed."}
    except Exception as exc:  # The complete report should retain each failed check.
        checks[name] = {"status": "FAIL", "message": f"{type(exc).__name__}: {exc}"}


def _assert_unique(df: pd.DataFrame, key: str, name: str) -> None:
    assert key in df.columns, f"{name}: missing primary key column {key}"
    assert df[key].notna().all(), f"{name}: null primary key"
    assert df[key].is_unique, f"{name}: duplicate primary key {key}"


def validate_data(
    master: MasterData,
    transactions: TransactionData,
    anomalous: AnomalousData,
    config: PipelineConfig = DEFAULT_CONFIG,
) -> dict[str, object]:
    """Validate keys, joins, authorization, event math, labels, evidence and lifecycle."""
    if not __debug__:
        raise RuntimeError("Validation requires Python assertions; do not run with -O.")
    checks: dict[str, dict[str, object]] = {}
    tables = [
        (master.stores, "store_id", "stores"), (master.skus, "sku_id", "skus"),
        (master.employees, "employee_id", "employees"),
        (master.employee_roles, "employee_role_id", "employee_roles"),
        (master.shifts, "shift_id", "shifts"), (master.promotions, "promo_id", "promotions"),
        (transactions.movements, "movement_id", "baseline movements"),
        (transactions.cycle_counts, "cycle_count_id", "baseline counts"),
        (transactions.returns, "return_id", "returns"),
        (anomalous.movements, "movement_id", "movements"),
        (anomalous.cycle_counts, "cycle_count_id", "cycle counts"),
        (anomalous.ground_truth, "anomaly_id", "ground truth"),
        (anomalous.event_history, "event_history_id", "event history"),
        (anomalous.detections, "detection_id", "detections"),
        (anomalous.shrinkage_alerts, "alert_id", "alerts"),
        (anomalous.investigations, "investigation_id", "investigations"),
    ]

    def primary_keys() -> None:
        for frame, key, label in tables:
            _assert_unique(frame, key, label)
        _assert_unique(anomalous.evaluation_metrics, "issue_type", "evaluation metrics")
        assert not master.opening_balances.duplicated(["store_id", "sku_id"]).any(), \
            "opening_balances: duplicate composite key"
        assert master.opening_balances[["store_id", "sku_id"]].notna().all().all(), \
            "opening_balances: null composite key"
        assert not anomalous.movements.drop(columns="movement_id").duplicated().any(), \
            "movements: duplicate business-event row"

    _run_check(checks, "primary_key_and_duplicate_integrity", primary_keys)

    def foreign_keys_and_joins() -> None:
        store_ids, sku_ids = set(master.stores.store_id), set(master.skus.sku_id)
        employee_ids = set(master.employees.employee_id)
        role_ids, shift_ids = set(master.employee_roles.employee_role_id), set(master.shifts.shift_id)
        movement_ids, count_ids = set(anomalous.movements.movement_id), set(anomalous.cycle_counts.cycle_count_id)
        assert set(master.employees.store_id) <= store_ids, "employees: unresolved store_id"
        assert set(master.employee_roles.employee_id) <= employee_ids, "employee_roles: unresolved employee_id"
        role_lookup = master.employee_roles.set_index("employee_role_id")
        employee_lookup = master.employees.set_index("employee_id")
        shift_role_employee = master.shifts.employee_role_id.map(role_lookup.employee_id)
        assert shift_role_employee.equals(master.shifts.employee_id), "shift employee does not match role assignment"
        assert master.shifts.employee_role_id.map(role_lookup.role_name).equals(master.shifts.role_name), "shift role does not match role assignment"
        assert set(master.shifts.store_id) <= store_ids, "shifts: unresolved store_id"
        assert set(master.shifts.employee_id) <= employee_ids, "shifts: unresolved employee_id"
        assert set(master.shifts.employee_role_id) <= role_ids, "shifts: unresolved employee_role_id"
        assert master.shifts.employee_id.map(employee_lookup.store_id).equals(master.shifts.store_id), "shift store does not match employee home store"
        assert set(master.promotions.sku_id) <= sku_ids, "promotions: unresolved sku_id"
        assert set(master.opening_balances.store_id) <= store_ids, "opening_balances: unresolved store_id"
        assert set(master.opening_balances.sku_id) <= sku_ids, "opening_balances: unresolved sku_id"
        for label, frame in (("movements", anomalous.movements), ("counts", anomalous.cycle_counts),
                             ("returns", anomalous.returns), ("alerts", anomalous.shrinkage_alerts),
                             ("ground_truth", anomalous.ground_truth)):
            assert set(frame.store_id) <= store_ids, f"{label}: unresolved store_id"
            assert set(frame.sku_id) <= sku_ids, f"{label}: unresolved sku_id"
        assert set(anomalous.movements.employee_id) <= employee_ids, "movements: unresolved employee_id"
        assert set(anomalous.movements.employee_role_id) <= role_ids, "movements: unresolved employee_role_id"
        assert set(anomalous.movements.shift_id) <= shift_ids, "movements: unresolved shift_id"
        assert anomalous.movements.employee_role_id.map(role_lookup.employee_id).equals(anomalous.movements.employee_id), "movement employee does not match role assignment"
        assert anomalous.movements.employee_role_id.map(role_lookup.role_name).equals(
            anomalous.movements.shift_id.map(master.shifts.set_index("shift_id").role_name)
        ), "movement role does not match linked shift role"
        assert set(anomalous.cycle_counts.movement_id) <= movement_ids, "counts: unresolved movement_id"
        assert set(anomalous.returns.movement_id) <= movement_ids, "returns: unresolved movement_id"
        assert set(anomalous.returns.original_sale_id) <= movement_ids, "returns: unresolved original_sale_id"
        assert set(anomalous.returns.employee_id) <= employee_ids, "returns: unresolved employee_id"
        assert set(anomalous.investigations.alert_id) <= set(anomalous.shrinkage_alerts.alert_id), "investigations: unresolved alert_id"
        for row in anomalous.shrinkage_alerts.itertuples(index=False):
            valid = row.target_id in movement_ids if row.entity_type == "InventoryMovement" else row.target_id in count_ids
            assert valid, f"alert {row.alert_id}: unresolved typed evidence target"
        for row in anomalous.ground_truth.itertuples(index=False):
            valid = row.target_id in movement_ids if row.entity_type == "InventoryMovement" else row.target_id in count_ids
            assert valid, f"ground_truth {row.anomaly_id}: unresolved typed target"
            target = (anomalous.movements.set_index("movement_id").loc[row.target_id]
                      if row.entity_type == "InventoryMovement"
                      else anomalous.cycle_counts.set_index("cycle_count_id").loc[row.target_id])
            assert (row.store_id, row.sku_id) == (target.store_id, target.sku_id), "ground_truth dimensions do not match target"
        investigation_ids = set(anomalous.investigations.investigation_id)
        for row in anomalous.event_history.itertuples(index=False):
            target_set = {"InventoryMovement": movement_ids, "CycleCount": count_ids,
                          "Investigation": investigation_ids}.get(row.entity_type, set())
            assert row.target_id in target_set, f"event_history {row.event_history_id}: unresolved typed target"
        related = anomalous.movements.loc[anomalous.movements.related_store_id.fillna("").ne(""), "related_store_id"]
        assert set(related) <= store_ids, "movements: unresolved related_store_id"

    _run_check(checks, "foreign_keys_and_modeled_joins", foreign_keys_and_joins)

    def enums() -> None:
        assert set(master.employees.employee_role) <= {"cashier", "stock_associate", "supervisor", "manager", "auditor"}
        assert set(master.employee_roles.role_name) <= {"cashier", "stock_associate", "supervisor", "manager", "auditor"}
        assert set(master.employee_roles.status) <= {"Active", "Inactive"}
        assert set(master.shifts.shift_type) <= {"Opening", "Midday", "Closing", "Overnight"}
        assert set(anomalous.movements.movement_type) <= {
            "Receipt", "Sale", "Return", "Adjustment", "Damage", "Transfer_Out", "Transfer_In", "Shrinkage"
        }
        assert set(anomalous.cycle_counts.location_type) <= {"Store", "Warehouse"}
        assert set(anomalous.ground_truth.issue_type) <= {
            "Wrong_Store_Transfer", "No_Reason_Adjustment", "Time_Travel_Count", "Double_Shrinkage_Transfer"
        }
        assert set(anomalous.investigations.status) <= {"Open", "In_Progress", "Closed"}
        assert set(anomalous.shrinkage_alerts.severity) <= {"Low", "Medium", "High", "Critical"}

    _run_check(checks, "allowed_enum_values", enums)

    def dates_and_authorization() -> None:
        assert (pd.to_datetime(master.stores.opened_date) <= pd.Timestamp(config.start_date)).all(), "store opened after simulation start"
        assert (pd.to_datetime(master.employees.hire_date) <= pd.Timestamp(config.start_date)).all(), "employee hired after simulation start"
        roles = master.employee_roles.set_index("employee_role_id")
        assert (pd.to_datetime(roles.effective_from) <= pd.to_datetime(roles.effective_to)).all(), "role effective date order invalid"
        shifts = master.shifts.set_index("shift_id")
        assert (pd.to_datetime(shifts.shift_start) < pd.to_datetime(shifts.shift_end)).all(), "shift end must follow start"
        assert (pd.to_datetime(shifts.shift_start).dt.strftime("%Y-%m-%d").to_numpy()
                == pd.to_datetime(shifts.shift_date).dt.strftime("%Y-%m-%d").to_numpy()).all(), "shift_date differs from shift start date"
        shift_attrs = shifts[["store_id", "employee_id", "employee_role_id", "role_name", "shift_start", "shift_end"]].add_suffix("_shift")
        linked = anomalous.movements.join(shift_attrs, on="shift_id")
        assert (linked.store_id == linked.store_id_shift).all(), "movement store does not match linked shift"
        assert (linked.employee_id == linked.employee_id_shift).all(), "movement employee does not match shift"
        assert (linked.employee_role_id == linked.employee_role_id_shift).all(), "movement role does not match shift"
        employee_store = master.employees.set_index("employee_id").store_id
        assert (linked.store_id == linked.employee_id.map(employee_store)).all(), "movement employee is not authorized for its store"
        stamp = pd.to_datetime(linked.timestamp)
        assert ((stamp >= pd.to_datetime(linked.shift_start_shift)) & (stamp < pd.to_datetime(linked.shift_end_shift))).all(), "movement is outside linked shift"
        allowed_role = {"Sale": "cashier", "Return": "cashier", "Receipt": "stock_associate",
                        "Transfer_Out": "stock_associate", "Transfer_In": "stock_associate",
                        "Adjustment": "supervisor", "Damage": "manager", "Shrinkage": "manager"}
        expected_role = linked.movement_type.map(allowed_role)
        # Anomaly shrinkage posts originate from an authorized stock-associate transfer.
        expected_role = expected_role.mask(linked.movement_type.eq("Shrinkage"), "stock_associate")
        assert (linked.role_name_shift == expected_role).all(), "movement type is not authorized for linked role"
        role_effective = roles.loc[linked.employee_role_id, ["effective_from", "effective_to", "status"]].reset_index(drop=True)
        assert (stamp.dt.normalize().reset_index(drop=True) >= pd.to_datetime(role_effective.effective_from)).all(), "movement precedes employee role assignment"
        assert (stamp.dt.normalize().reset_index(drop=True) <= pd.to_datetime(role_effective.effective_to)).all(), "movement follows employee role assignment"
        assert role_effective.status.eq("Active").all(), "movement linked to inactive employee role"
        assert (pd.to_datetime(master.promotions.start_date) <= pd.to_datetime(master.promotions.end_date)).all(), "promotion date order invalid"
        assert master.promotions.discount_rate.between(0, 1, inclusive="right").all(), "promotion discount must be greater than zero and at most one"
        return_rows = anomalous.returns.merge(
            anomalous.movements[["movement_id", "timestamp", "qty_change", "movement_type"]],
            on="movement_id", suffixes=("", "_movement"), validate="one_to_one",
        )
        sale_rows = anomalous.movements.set_index("movement_id")
        sale_time = return_rows.original_sale_id.map(sale_rows.timestamp)
        sale_qty = return_rows.original_sale_id.map(sale_rows.qty_change).abs()
        sale_type = return_rows.original_sale_id.map(sale_rows.movement_type)
        assert sale_type.eq("Sale").all(), "Return original_sale_id must reference a Sale movement"
        assert (pd.to_datetime(return_rows.return_timestamp) > pd.to_datetime(sale_time)).all(), "return precedes original sale"
        assert (return_rows.return_qty <= sale_qty).all(), "return exceeds original sale quantity"
        assert return_rows.movement_type.eq("Return").all(), "Return entity is not linked to Return movement"
        assert (pd.to_datetime(return_rows.return_timestamp) == pd.to_datetime(return_rows.timestamp)).all(), "Return timestamp differs from linked movement timestamp"
        original_sales = anomalous.movements.set_index("movement_id")
        assert (return_rows.store_id == return_rows.original_sale_id.map(original_sales.store_id)).all(), "return store differs from original sale"
        assert (return_rows.sku_id == return_rows.original_sale_id.map(original_sales.sku_id)).all(), "return SKU differs from original sale"
        assert not return_rows.original_sale_id.duplicated().any(), "generator policy permits at most one Return entity per Sale"
        returned_by_sale = return_rows.groupby("original_sale_id").return_qty.sum()
        assert (returned_by_sale <= returned_by_sale.index.to_series().map(sale_rows.qty_change).abs().to_numpy()).all(), "aggregate return quantity exceeds sold quantity"
        return_movement_ids = set(anomalous.movements.loc[anomalous.movements.movement_type.eq("Return"), "movement_id"])
        assert set(anomalous.returns.movement_id) == return_movement_ids, "every Return movement must have one Return entity"
        assert anomalous.returns.status.isin({"Accepted", "Rejected", "Reviewed"}).all(), "invalid Return lifecycle status"

    _run_check(checks, "date_order_and_employee_shift_authorization", dates_and_authorization)

    def baseline_reconciliation() -> None:
        frame = transactions.movements.sort_values(["store_id", "sku_id", "timestamp", "movement_id"], kind="mergesort").copy()
        frame["running_net"] = frame.groupby(["store_id", "sku_id"], sort=False).qty_change.cumsum()
        opening = master.opening_balances.set_index(["store_id", "sku_id"]).opening_qty
        keys = pd.MultiIndex.from_arrays([frame.store_id, frame.sku_id])
        frame["running_qty"] = frame.running_net.to_numpy(dtype=np.int64) + opening.reindex(keys).to_numpy(dtype=np.int64)
        assert (frame.running_qty >= 0).all(), "baseline ledger has negative running stock"
        final = frame.groupby(["store_id", "sku_id"], sort=False).tail(1)
        assert len(final) == len(master.opening_balances), "not every opening pair has movement coverage"
        assert (final.running_qty == final.running_net + opening.reindex(pd.MultiIndex.from_arrays([final.store_id, final.sku_id])).to_numpy()).all(), "ending stock equation does not reconcile"
        count_rows = transactions.cycle_counts
        by_movement = frame.set_index("movement_id")
        expected = count_rows.movement_id.map(by_movement.running_qty)
        assert (expected.to_numpy() == count_rows.system_qty.to_numpy()).all(), "count system_qty is not the ledger balance at its referenced movement"
        assert (count_rows.variance_qty == count_rows.counted_qty - count_rows.system_qty).all(), "count variance arithmetic invalid"
        assert (count_rows.counted_qty >= 0).all(), "negative physical count"
        assert (count_rows.timestamp.to_numpy() == count_rows.movement_id.map(by_movement.timestamp).to_numpy()).all(), "baseline count timestamp differs from referenced movement"
        assert (count_rows.store_id == count_rows.movement_id.map(by_movement.store_id)).all(), "baseline count store differs from referenced movement"
        assert (count_rows.sku_id == count_rows.movement_id.map(by_movement.sku_id)).all(), "baseline count SKU differs from referenced movement"

    _run_check(checks, "baseline_inventory_and_count_reconciliation", baseline_reconciliation)

    def transfer_and_return_rules() -> None:
        transfers = transactions.movements.loc[transactions.movements.transfer_id.fillna("").ne("")]
        groups = transfers.groupby("transfer_id")
        assert len(groups) > 0, "no legitimate transfer pairs generated"
        for transfer_id, group in groups:
            assert len(group) == 2, f"{transfer_id}: expected exactly two transfer legs"
            out = group.loc[group.movement_type.eq("Transfer_Out")]
            incoming = group.loc[group.movement_type.eq("Transfer_In")]
            assert len(out) == len(incoming) == 1, f"{transfer_id}: missing paired direction"
            assert int(out.iloc[0].qty_change) + int(incoming.iloc[0].qty_change) == 0, f"{transfer_id}: transfer quantities do not balance"
            assert out.iloc[0].related_store_id == incoming.iloc[0].store_id, f"{transfer_id}: wrong outbound destination"
            assert incoming.iloc[0].related_store_id == out.iloc[0].store_id, f"{transfer_id}: wrong inbound origin"
            assert out.iloc[0].store_id != incoming.iloc[0].store_id, f"{transfer_id}: transfer endpoints must be different stores"
            assert out.iloc[0].timestamp <= incoming.iloc[0].timestamp, f"{transfer_id}: receipt precedes dispatch"
        assert set(transactions.returns.movement_id) <= set(transactions.movements.loc[transactions.movements.movement_type.eq("Return"), "movement_id"])

    _run_check(checks, "paired_transfers_and_return_lineage", transfer_and_return_rules)

    def anomaly_and_evidence_rules() -> None:
        expected = {"Wrong_Store_Transfer", "No_Reason_Adjustment", "Time_Travel_Count", "Double_Shrinkage_Transfer"}
        assert set(anomalous.ground_truth.issue_type) == expected, "one or more required anomaly categories are absent"
        assert not anomalous.ground_truth.duplicated(["entity_type", "target_id", "issue_type"]).any(), "duplicate anomaly labels"
        assert set(zip(anomalous.detections.entity_type, anomalous.detections.target_id, anomalous.detections.issue_type)) == set(
            zip(anomalous.ground_truth.entity_type, anomalous.ground_truth.target_id, anomalous.ground_truth.issue_type)
        ), "independent detector output does not exactly match planted labels"
        assert not {"issue_type", "anomaly_id", "ground_truth"}.intersection(anomalous.movements.columns), "ground-truth labels leaked into movement features"
        alert_detection = anomalous.shrinkage_alerts.merge(
            anomalous.detections, on="detection_id", suffixes=("_alert", "_detection"),
            validate="one_to_one",
        )
        assert len(alert_detection) == len(anomalous.detections), "each detection must map to exactly one alert"
        for column in ("entity_type", "target_id", "issue_type", "store_id", "sku_id", "risk_score", "severity"):
            assert alert_detection[f"{column}_alert"].equals(alert_detection[f"{column}_detection"]), f"alert {column} differs from detection"
        assert anomalous.shrinkage_alerts.alert_reason.fillna("").str.len().gt(0).all(), "alert has no evidence reason"
        expected_scores = (0.5 + 0.5 * anomalous.detections.loss_qty.astype(float)
                           .div(anomalous.detections.loss_qty.astype(float) + 10.0)).round(8)
        assert np.allclose(anomalous.detections.risk_score, expected_scores, rtol=0, atol=0), "risk score is not derived from loss evidence"
        alert_scores = anomalous.shrinkage_alerts.set_index("detection_id").risk_score
        detection_scores = anomalous.detections.set_index("detection_id").risk_score
        assert alert_scores.equals(detection_scores), "alert risk score differs from source detection"
        assert len(anomalous.investigations) == len(anomalous.shrinkage_alerts), "every alert must have one investigation"
        assert anomalous.investigations.alert_id.is_unique, "one investigation is expected per alert"
        assert anomalous.investigations.resolution_findings.fillna("").str.len().gt(0).all(), "investigation has no evidence-based finding"
        investigation_history = anomalous.event_history.loc[
            anomalous.event_history.entity_type.eq("Investigation")
        ]
        allowed_transitions = {("Open", "In_Progress"), ("In_Progress", "Closed")}
        for investigation in anomalous.investigations.itertuples(index=False):
            events = investigation_history.loc[
                investigation_history.investigation_id.eq(investigation.investigation_id)
            ].sort_values("version", kind="mergesort")
            assert len(events) >= 1 and int(events.iloc[0].version) == 1, "investigation history must start at version 1"
            initial = json.loads(events.iloc[0].after_json)
            assert initial["status"] == "Open", "investigation lifecycle must start Open"
            previous = "Open"
            expected_version = 2
            for event in events.iloc[1:].itertuples(index=False):
                before, after = json.loads(event.before_json), json.loads(event.after_json)
                transition = (before.get("status"), after.get("status"))
                assert transition in allowed_transitions, f"invalid investigation transition {transition}"
                assert before.get("status") == previous, "investigation history has a discontinuous state"
                assert int(event.version) == expected_version, "investigation history version is not consecutive"
                assert event.event_type == "STATUS_TRANSITION", "lifecycle change has wrong event type"
                previous = after["status"]
                expected_version += 1
            assert previous == investigation.status, "investigation current status differs from event history"
        expected_metrics = evaluate_detections(
            anomalous.ground_truth, anomalous.detections,
            anomalous.movements, anomalous.cycle_counts,
        )
        metric_columns = list(expected_metrics.columns)
        observed_metrics = anomalous.evaluation_metrics[metric_columns].sort_values(
            "issue_type", kind="mergesort"
        ).reset_index(drop=True)
        expected_metrics = expected_metrics.sort_values("issue_type", kind="mergesort").reset_index(drop=True)
        pd.testing.assert_frame_equal(observed_metrics, expected_metrics, check_dtype=False, check_exact=True)
        assert set(anomalous.evaluation_metrics.issue_type) == expected | {"Overall_Micro"}, "missing category evaluation metrics"
        assert ((anomalous.evaluation_metrics.precision >= 0) & (anomalous.evaluation_metrics.precision <= 1)).all()
        assert ((anomalous.evaluation_metrics.recall >= 0) & (anomalous.evaluation_metrics.recall <= 1)).all()
        assert ((anomalous.evaluation_metrics.f1_score >= 0) & (anomalous.evaluation_metrics.f1_score <= 1)).all()
        assert not anomalous.event_history.empty, "anomaly mutations must preserve before/after event history"
        for entry in anomalous.event_history.itertuples(index=False):
            if entry.event_type == "ANOMALY_INJECTION":
                assert json.loads(entry.before_json) != json.loads(entry.after_json), "event history does not preserve a changed state"

    _run_check(checks, "anomaly_detection_evidence_and_evaluation", anomaly_and_evidence_rules)

    def id_formats() -> None:
        patterns = [
            (master.stores.store_id, r"STORE-\d{6}"), (master.skus.sku_id, r"SKU-\d{6}"),
            (master.employees.employee_id, r"EMP-\d{7}"),
            (master.employee_roles.employee_role_id, r"ER-\d{8}"),
            (master.shifts.shift_id, r"SHIFT-\d{8}"),
            (master.promotions.promo_id, r"PROMO-\d{6}"),
            (transactions.movements.movement_id, r"MOV-2026-\d{8}"),
            (transactions.cycle_counts.cycle_count_id, r"CNT-2026-\d{6}"),
            (anomalous.returns.return_id, r"RET-2026-\d{8}"),
            (anomalous.ground_truth.anomaly_id, r"ANOM-\d{6}"),
            (anomalous.event_history.event_history_id, r"HIST-\d{8}"),
            (anomalous.detections.detection_id, r"DET-\d{8}"),
            (anomalous.shrinkage_alerts.alert_id, r"SHR-2026-\d{6}"),
            (anomalous.investigations.investigation_id, r"INV-2026-\d{6}"),
        ]
        for values, pattern in patterns:
            assert values.astype(str).str.fullmatch(pattern).all(), f"ID format invalid: {pattern}"

    _run_check(checks, "required_id_formats", id_formats)

    failed = [name for name, result in checks.items() if result["status"] == "FAIL"]
    summary: dict[str, object] = {
        "seed": config.seed, "scale": config.scale,
        "overall_status": "FAIL" if failed else "PASS",
        "passed": len(checks) - len(failed), "failed": len(failed), "checks": checks,
    }
    config.output_dir.mkdir(parents=True, exist_ok=True)
    config.validation_summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compare_hash_manifests(first: Mapping[str, str], second: Mapping[str, str]) -> bool:
    """Require identical artifact names and SHA-256 digests."""
    if not first or set(first) != set(second):
        raise AssertionError("Artifact manifests have different or empty key sets")
    if dict(first) != dict(second):
        differing = sorted(key for key in first if first[key] != second.get(key))
        raise AssertionError(f"Output hashes differ: {differing}")
    return True
