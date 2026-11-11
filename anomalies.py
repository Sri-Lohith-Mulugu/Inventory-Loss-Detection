from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import DEFAULT_CONFIG, PipelineConfig
from detector import detect_anomalies
from evaluation import evaluate_detections
from master_data import MasterData
from transactions import TransactionData


@dataclass(frozen=True)
class AnomalousData:
    movements: pd.DataFrame
    cycle_counts: pd.DataFrame
    returns: pd.DataFrame
    ground_truth: pd.DataFrame
    event_history: pd.DataFrame
    detections: pd.DataFrame
    shrinkage_alerts: pd.DataFrame
    investigations: pd.DataFrame
    evaluation_metrics: pd.DataFrame


def inject_anomalies(
    master: MasterData, transactions: TransactionData,
    config: PipelineConfig = DEFAULT_CONFIG,
) -> AnomalousData:
    """Mutate selected baseline events, retaining labels and before/after history."""
    rng = np.random.default_rng(config.seed + 20)
    movements = transactions.movements.copy(deep=True).reset_index(drop=True)
    counts = transactions.cycle_counts.copy(deep=True).reset_index(drop=True)
    records: list[dict[str, str]] = []
    history: list[dict[str, object]] = []

    def capture(kind: str, entity: str, target: str, frame: pd.DataFrame, idx: int,
                changes: dict[str, object], details: str, *, label: bool = True) -> None:
        before = {column: frame.at[idx, column] for column in changes}
        for column, value in changes.items():
            frame.at[idx, column] = value
        after = {column: frame.at[idx, column] for column in changes}
        if label:
            records.append({"anomaly_id": f"ANOM-{len(records) + 1:06d}", "entity_type": entity,
                            "target_id": target, "issue_type": kind, "store_id": str(frame.at[idx, "store_id"]),
                            "sku_id": str(frame.at[idx, "sku_id"]), "details": details,
                            "movement_id": target if entity == "InventoryMovement" else None,
                            "cycle_count_id": target if entity == "CycleCount" else None})
        history.append({
            "event_history_id": f"HIST-{len(history) + 1:08d}", "entity_type": entity,
            "target_id": target, "version": 2, "event_type": "ANOMALY_INJECTION",
            "movement_id": target if entity == "InventoryMovement" else None,
            "cycle_count_id": target if entity == "CycleCount" else None,
            "investigation_id": None,
            "event_timestamp": str(frame.at[idx, "timestamp"]),
            "before_json": json.dumps(before, sort_keys=True, default=str),
            "after_json": json.dumps(after, sort_keys=True, default=str),
            "reason_code": kind,
        })

    transfer_out = movements.index[movements.movement_type.eq("Transfer_Out")]
    used_transfer_ids: set[str] = set()
    # A real destination exists, but the outbound relationship points elsewhere;
    # the paired receipt remains at the original intended destination.
    if len(transfer_out):
        idx = int(transfer_out[0])
        row = movements.loc[idx]
        transfer_id = str(row.transfer_id)
        inbound = movements.loc[movements.transfer_id.eq(transfer_id) & movements.movement_type.eq("Transfer_In")]
        if len(inbound):
            actual_destination = str(inbound.iloc[0].store_id)
            alternatives = [store for store in master.stores.store_id
                           if store not in {str(row.store_id), actual_destination}]
            # With exactly two stores, the origin itself is still a wrong
            # destination relative to the paired receipt and remains a valid FK.
            wrong_destination = alternatives[0] if alternatives else str(row.store_id)
            capture("Wrong_Store_Transfer", "InventoryMovement", str(row.movement_id), movements,
                    idx, {"related_store_id": wrong_destination},
                    f"Destination {wrong_destination} differs from paired receipt store {actual_destination}.")
            used_transfer_ids.add(transfer_id)

    # A specific set of supervisor employees performs a cluster of unexplained losses.
    negative_adjustments = movements.loc[
        movements.movement_type.eq("Adjustment") & movements.qty_change.lt(0)
    ]
    employee_roles = master.employee_roles.set_index("employee_role_id").role_name
    supervisor_ids = set(employee_roles.index[employee_roles.eq("supervisor")])
    supervisor_adjustments = negative_adjustments.loc[
        negative_adjustments.employee_role_id.isin(supervisor_ids)
    ]
    targeted_employees = (supervisor_adjustments.groupby("employee_id", sort=True).size()
                          .sort_values(ascending=False, kind="mergesort").head(3).index.tolist())
    candidates = supervisor_adjustments.index[
        supervisor_adjustments.employee_id.isin(targeted_employees)
    ]
    if not len(candidates):
        raise ValueError("No authorized negative supervisor adjustments available for anomaly injection")
    for idx in candidates[:6]:
        row = movements.loc[int(idx)]
        employee = str(row.employee_id)
        capture("No_Reason_Adjustment", "InventoryMovement", str(row.movement_id), movements,
                int(idx), {"approval_code": "", "reason_code": ""},
                f"Negative adjustment by targeted employee {employee} has no approval or reason.")

    # Shift one count to before a large receipt and preserve the original event values.
    receipt_ids = set(movements.loc[movements.movement_type.eq("Receipt"), "movement_id"])
    eligible = counts.index[counts.movement_id.isin(receipt_ids)]
    if len(eligible):
        ci = int(eligible[int(rng.integers(0, len(eligible)))])
        count = counts.loc[ci]
        mi = int(movements.index[movements.movement_id.eq(count.movement_id)][0])
        movement = movements.loc[mi]
        old_qty = int(movement.qty_change)
        large_qty = 10_000
        capture("Time_Travel_Count", "CycleCount", str(count.cycle_count_id), counts, ci, {
            "timestamp": pd.Timestamp(movement.timestamp) - pd.Timedelta(days=1),
            "system_qty": int(count.system_qty) + large_qty - old_qty,
            "counted_qty": 0,
            "variance_qty": -(int(count.system_qty) + large_qty - old_qty),
        }, f"Count precedes its referenced receipt {movement.movement_id}; receipt qty changed to {large_qty}.")
        capture("Time_Travel_Count", "InventoryMovement", str(movement.movement_id), movements, mi,
                {"qty_change": large_qty}, "Receipt magnitude increased to 10,000 after the linked count timestamp.", label=False)

    # Both legs of another valid transfer become loss postings, preserving its shared transfer key.
    candidate_pairs = movements.loc[
        movements.transfer_id.ne("") & movements.movement_type.isin(["Transfer_Out", "Transfer_In"])
    ].groupby("transfer_id", sort=True)
    for transfer_id, group in candidate_pairs:
        if transfer_id in used_transfer_ids or set(group.movement_type) != {"Transfer_Out", "Transfer_In"}:
            continue
        if group.store_id.nunique() != 2 or len(group) != 2:
            continue
        for idx in group.index:
            row = movements.loc[int(idx)]
            capture("Double_Shrinkage_Transfer", "InventoryMovement", str(row.movement_id), movements,
                    int(idx), {"movement_type": "Shrinkage", "qty_change": -1,
                               "reason_code": "TRANSFER_LOSS"},
                    f"Transfer {transfer_id} has a shrinkage loss posting at each endpoint.")
        used_transfer_ids.add(str(transfer_id))
        break

    ground_truth = pd.DataFrame(records, columns=[
        "anomaly_id", "entity_type", "target_id", "issue_type", "store_id", "sku_id", "details",
        "movement_id", "cycle_count_id",
    ])
    history_frame = pd.DataFrame(history, columns=[
        "event_history_id", "entity_type", "target_id", "movement_id", "cycle_count_id",
        "investigation_id", "version", "event_type",
        "event_timestamp", "before_json", "after_json", "reason_code",
    ])
    detections = detect_anomalies(movements, counts, master.stores, config)
    detections["movement_id"] = detections.target_id.where(detections.entity_type.eq("InventoryMovement"))
    detections["cycle_count_id"] = detections.target_id.where(detections.entity_type.eq("CycleCount"))
    detections["detection_timestamp"] = pd.to_datetime(detections.detection_timestamp)
    alerts = detections.rename(columns={"detection_id": "alert_id", "detection_timestamp": "timestamp",
        "evidence": "alert_reason"}).copy()
    alerts["detection_id"] = detections.detection_id.to_numpy()
    alerts["alert_id"] = [f"SHR-2026-{i:06d}" for i in range(1, len(alerts) + 1)]
    alerts["movement_id"] = alerts.target_id.where(alerts.entity_type.eq("InventoryMovement"))
    alerts["cycle_count_id"] = alerts.target_id.where(alerts.entity_type.eq("CycleCount"))
    alert_columns = ["alert_id", "detection_id", "timestamp", "entity_type", "target_id", "movement_id", "cycle_count_id", "issue_type", "store_id",
                     "sku_id", "loss_qty", "risk_score", "severity", "alert_reason"]
    alerts = alerts[alert_columns]
    status_by_issue = {
        "Wrong_Store_Transfer": "In_Progress",
        "No_Reason_Adjustment": "In_Progress",
        "Time_Travel_Count": "Open",
        "Double_Shrinkage_Transfer": "Closed",
    }
    final_statuses = alerts.issue_type.map(status_by_issue)
    finding_by_issue = {
        "Wrong_Store_Transfer": "Destination mismatch requires transfer-route review.",
        "No_Reason_Adjustment": "Missing approval and reason require manager review.",
        "Time_Travel_Count": "Count precedes receipt; recount is pending.",
        "Double_Shrinkage_Transfer": "Synthetic paired endpoint loss confirmed from shared transfer evidence.",
    }
    investigations = pd.DataFrame({
        "investigation_id": [f"INV-2026-{i:06d}" for i in range(1, len(alerts) + 1)],
        "alert_id": alerts.alert_id.to_numpy(),
        "status": final_statuses.to_numpy(),
        "resolution_findings": [
            f"{finding_by_issue[issue]} Evidence: {reason}"
            for issue, reason in zip(alerts.issue_type, alerts.alert_reason)
        ],
    })
    if len(investigations):
        lifecycle_events: list[dict[str, object]] = []
        next_history_id = len(history_frame) + 1
        for index, (investigation, alert) in enumerate(
            zip(investigations.itertuples(index=False), alerts.itertuples(index=False))
        ):
            states = ["Open"]
            if investigation.status == "In_Progress":
                states.append("In_Progress")
            elif investigation.status == "Closed":
                states.extend(["In_Progress", "Closed"])
            created_at = pd.Timestamp(alert.timestamp)
            previous = "Open"
            initial_after = json.dumps(
                {"status": "Open", "alert_id": investigation.alert_id}, sort_keys=True
            )
            lifecycle_events.append({
                "event_history_id": f"HIST-{next_history_id:08d}",
                "entity_type": "Investigation", "target_id": investigation.investigation_id,
                "movement_id": None, "cycle_count_id": None,
                "investigation_id": investigation.investigation_id,
                "version": 1, "event_type": "STATUS_INITIALIZED",
                "event_timestamp": created_at, "before_json": "null",
                "after_json": initial_after, "reason_code": "DETECTION_CREATED",
            })
            next_history_id += 1
            for version_number, next_status in enumerate(states[1:], start=2):
                event_time = created_at + pd.Timedelta(minutes=version_number - 1)
                before = {"status": previous, "alert_id": investigation.alert_id}
                after = {"status": next_status, "alert_id": investigation.alert_id}
                lifecycle_events.append({
                    "event_history_id": f"HIST-{next_history_id:08d}",
                    "entity_type": "Investigation", "target_id": investigation.investigation_id,
                    "movement_id": None, "cycle_count_id": None,
                    "investigation_id": investigation.investigation_id,
                    "version": version_number, "event_type": "STATUS_TRANSITION",
                    "event_timestamp": event_time,
                    "before_json": json.dumps(before, sort_keys=True),
                    "after_json": json.dumps(after, sort_keys=True),
                    "reason_code": f"{previous.upper()}_TO_{next_status.upper()}",
                })
                next_history_id += 1
                previous = next_status
        history_frame = pd.concat(
            [history_frame, pd.DataFrame(lifecycle_events, columns=history_frame.columns)],
            ignore_index=True,
        )
    metrics = evaluate_detections(ground_truth, detections, movements, counts)
    return AnomalousData(movements, counts, transactions.returns.copy(), ground_truth,
                         history_frame, detections, alerts, investigations, metrics)
