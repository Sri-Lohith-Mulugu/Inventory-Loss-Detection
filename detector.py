from __future__ import annotations

import numpy as np
import pandas as pd

from config import DEFAULT_CONFIG, PipelineConfig


DETECTION_COLUMNS = ["detection_id", "entity_type", "target_id", "issue_type", "store_id",
                     "sku_id", "detection_timestamp", "loss_qty", "risk_score", "severity", "evidence"]


def detect_anomalies(
    movements: pd.DataFrame, cycle_counts: pd.DataFrame, stores: pd.DataFrame,
    config: PipelineConfig = DEFAULT_CONFIG,
) -> pd.DataFrame:
    """Apply deterministic evidence rules without receiving ground-truth labels."""
    found: list[dict[str, object]] = []

    outbound = movements.loc[movements.movement_type.eq("Transfer_Out")]
    inbound = movements.loc[movements.movement_type.eq("Transfer_In")]
    if len(outbound):
        paired = outbound.merge(
            inbound[["transfer_id", "store_id", "sku_id", "movement_id"]],
            on="transfer_id", how="left", suffixes=("_out", "_in"), validate="one_to_one",
        )
        mismatch = paired.transfer_id.ne("") & (
            paired.store_id_in.isna()
            | paired.related_store_id.fillna("").ne(paired.store_id_in.fillna(""))
            | paired.sku_id_out.ne(paired.sku_id_in.fillna(""))
        )
        for row in paired.loc[mismatch].itertuples(index=False):
            found.append({"entity_type": "InventoryMovement", "target_id": row.movement_id_out,
                "issue_type": "Wrong_Store_Transfer", "store_id": row.store_id_out,
                "sku_id": row.sku_id_out, "detection_timestamp": row.timestamp,
                "loss_qty": abs(int(row.qty_change)), "evidence":
                    f"Transfer {row.transfer_id}: outbound destination {row.related_store_id!r} does not match receipt store {row.store_id_in!r}.",
            })

    no_reason = movements.loc[
        movements.movement_type.eq("Adjustment") & movements.qty_change.lt(0)
        & (movements.reason_code.fillna("").str.strip().eq("")
           | movements.approval_code.fillna("").str.strip().eq(""))
    ]
    for row in no_reason.itertuples(index=False):
        found.append({"entity_type": "InventoryMovement", "target_id": row.movement_id,
            "issue_type": "No_Reason_Adjustment", "store_id": row.store_id, "sku_id": row.sku_id,
            "detection_timestamp": row.timestamp, "loss_qty": abs(int(row.qty_change)),
            "evidence": f"Negative adjustment lacks approval or reason metadata (employee {row.employee_id})."})

    movement_lookup = movements.set_index("movement_id")
    if len(cycle_counts):
        linked_time = cycle_counts["movement_id"].map(movement_lookup["timestamp"])
        time_travel = pd.to_datetime(cycle_counts.timestamp) < pd.to_datetime(linked_time)
        for row in cycle_counts.loc[time_travel].itertuples(index=False):
            linked_ts = movement_lookup.at[row.movement_id, "timestamp"]
            found.append({"entity_type": "CycleCount", "target_id": row.cycle_count_id,
                "issue_type": "Time_Travel_Count", "store_id": row.store_id, "sku_id": row.sku_id,
                "detection_timestamp": row.timestamp, "loss_qty": abs(int(row.variance_qty)),
                "evidence": f"Count time {row.timestamp} precedes referenced movement {row.movement_id} at {linked_ts}.",
            })

    shrink = movements.loc[movements.movement_type.eq("Shrinkage") & movements.transfer_id.fillna("").ne("")]
    if len(shrink):
        transfer_store_counts = shrink.groupby("transfer_id").store_id.nunique()
        double_ids = set(transfer_store_counts[transfer_store_counts.ge(2)].index)
        for row in shrink.loc[shrink.transfer_id.isin(double_ids)].itertuples(index=False):
            found.append({"entity_type": "InventoryMovement", "target_id": row.movement_id,
                "issue_type": "Double_Shrinkage_Transfer", "store_id": row.store_id,
                "sku_id": row.sku_id, "detection_timestamp": row.timestamp,
                "loss_qty": abs(int(row.qty_change)),
                "evidence": f"Transfer {row.transfer_id} is recorded as shrinkage at multiple stores."})

    if not found:
        return pd.DataFrame(columns=DETECTION_COLUMNS)
    result = pd.DataFrame(found).sort_values(
        ["detection_timestamp", "issue_type", "target_id"], kind="mergesort"
    ).reset_index(drop=True)
    result.insert(0, "detection_id", [f"DET-{i:08d}" for i in range(1, len(result) + 1)])
    loss = result.loss_qty.astype(float)
    result["risk_score"] = (0.5 + 0.5 * loss.div(loss + 10.0)).round(8)
    result["severity"] = np.where(result.loss_qty.ge(100), "Critical",
                         np.where(result.loss_qty.ge(10), "High", "Medium"))
    return result[DETECTION_COLUMNS]
