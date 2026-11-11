from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import DEFAULT_CONFIG, PipelineConfig
from master_data import MasterData


@dataclass(frozen=True)
class TransactionData:
    movements: pd.DataFrame
    cycle_counts: pd.DataFrame
    returns: pd.DataFrame


def _movement_roles(movement_types: np.ndarray) -> np.ndarray:
    role_by_type = {
        "Sale": "cashier", "Return": "cashier", "Receipt": "stock_associate",
        "Transfer_In": "stock_associate", "Transfer_Out": "stock_associate",
        "Adjustment": "supervisor", "Damage": "manager", "Shrinkage": "manager",
    }
    return np.fromiter((role_by_type[x] for x in movement_types), dtype=object, count=len(movement_types))


def generate_transactions(
    master: MasterData, config: PipelineConfig = DEFAULT_CONFIG
) -> TransactionData:
    """Generate a deterministic, authorized event ledger and linked physical returns.

    Event quantities and entity keys are generated in vectorized batches. Store/SKU
    Each Store/SKU pair receives a baseline event; remaining events are allocated
    with deterministic store- and category-weighted demand to create varied workload.
    Legitimate transfers are represented by paired, equal-quantity legs.
    """
    n = config.movement_count
    if n < 2:
        raise ValueError("movement_count must be at least 2 to create paired transfer events")
    rng = np.random.default_rng(config.seed + 10)
    days = pd.date_range(config.start_date, config.end_date, freq="D")
    day_count = len(days)
    store_sku_pairs = config.store_count * config.sku_count
    base_pair_ids = np.arange(store_sku_pairs, dtype=np.int64)
    extra_count = n - store_sku_pairs
    if extra_count:
        category_demand = {
            "Grocery": 1.45, "Household": 1.10, "Electronics": 0.55,
            "Apparel": 0.80, "Health": 1.05, "Beauty": 0.90,
        }
        sku_categories = master.skus.sort_values("sku_id").category.to_numpy()
        sku_demand = np.fromiter(
            (category_demand[str(category)] for category in sku_categories),
            dtype=float, count=config.sku_count,
        )
        sku_demand *= rng.lognormal(mean=0.0, sigma=0.25, size=config.sku_count)
        store_demand = rng.lognormal(mean=0.0, sigma=0.18, size=config.store_count)
        pair_weights = (store_demand[:, None] * sku_demand[None, :]).ravel()
        pair_weights /= pair_weights.sum()
        extra_pair_ids = rng.choice(store_sku_pairs, size=extra_count, replace=True, p=pair_weights)
        pair_ids = np.concatenate([base_pair_ids, extra_pair_ids.astype(np.int64)])
    else:
        pair_ids = base_pair_ids
    rng.shuffle(pair_ids)
    store_num = pair_ids // config.sku_count + 1
    sku_num = pair_ids % config.sku_count + 1
    day_pos = rng.integers(0, day_count, size=n, dtype=np.int32)

    kinds = np.array(["Receipt", "Sale", "Return", "Adjustment", "Damage"], dtype=object)
    movement_type = rng.choice(kinds, size=n, p=[0.23, 0.56, 0.06, 0.11, 0.04])
    magnitude = rng.integers(1, 4, size=n, dtype=np.int32)
    qty_change = np.where(np.isin(movement_type, ["Receipt", "Return"]), magnitude,
                          np.where(movement_type == "Adjustment",
                                   np.where(rng.random(n) < 0.5, magnitude, -1), -1))
    qty_change = qty_change.astype(np.int64)
    # Clean damage is an explained, approved operational loss.
    approval_code = np.where(np.isin(movement_type, ["Adjustment", "Damage"]), "APR-BASE", "")
    reason_code = np.where(movement_type == "Adjustment", "ROUTINE",
                           np.where(movement_type == "Damage", "DAMAGED_GOODS", ""))

    # Turn existing events into legitimate transfer pairs, preserving row volume.
    transfer_ids = np.full(n, "", dtype=object)
    related_store_ids = np.full(n, "", dtype=object)
    negative_rows = np.flatnonzero((movement_type == "Sale") & (qty_change < 0))
    receipt_rows = np.flatnonzero(movement_type == "Receipt")
    receipt_by_sku: dict[int, list[int]] = {}
    for idx in receipt_rows:
        receipt_by_sku.setdefault(int(sku_num[idx]), []).append(int(idx))
    used_receipts: set[int] = set()
    transfer_pairs: list[tuple[int, int]] = []
    desired_pairs = max(2, n // 1000)
    rng.shuffle(negative_rows)
    for out_idx in negative_rows:
        options = receipt_by_sku.get(int(sku_num[out_idx]), ())
        in_idx = next((candidate for candidate in options
                       if candidate not in used_receipts
                       and store_num[candidate] != store_num[out_idx]), None)
        if in_idx is None:
            continue
        used_receipts.add(in_idx)
        transfer_pairs.append((int(out_idx), int(in_idx)))
        if len(transfer_pairs) >= desired_pairs:
            break
    if not transfer_pairs:
        # Ensure even the smallest configured profile has one genuine transfer.
        out_idx = 0
        candidates = np.flatnonzero(
            (sku_num == sku_num[out_idx]) & (store_num != store_num[out_idx])
        )
        if not len(candidates):
            raise ValueError("Could not construct a clean paired transfer from generated events")
        in_idx = int(candidates[0])
        transfer_pairs.append((out_idx, in_idx))
    for sequence, (out_idx, in_idx) in enumerate(transfer_pairs, start=1):
        transfer_id = f"TRF-{sequence:08d}"
        movement_type[out_idx], movement_type[in_idx] = "Transfer_Out", "Transfer_In"
        qty_change[out_idx], qty_change[in_idx] = -1, 1
        transfer_ids[out_idx] = transfer_id
        transfer_ids[in_idx] = transfer_id
        related_store_ids[out_idx] = f"STORE-{int(store_num[in_idx]):06d}"
        related_store_ids[in_idx] = f"STORE-{int(store_num[out_idx]):06d}"
        day_pos[in_idx] = day_pos[out_idx]

    roles = _movement_roles(movement_type)
    role_order = ("cashier", "stock_associate", "supervisor", "manager", "auditor")
    role_index = {name: idx for idx, name in enumerate(role_order)}
    role_pos = np.fromiter((role_index[x] for x in roles), dtype=np.int32, count=n)
    # Shifts are emitted in store/day/role order by generate_shifts().
    shift_pos = ((store_num - 1) * day_count + day_pos) * len(role_order) + role_pos
    shifts = master.shifts
    if len(shifts) <= int(shift_pos.max()):
        raise ValueError("Shift schedule does not cover every generated movement date and role")
    shift_ids = shifts.shift_id.to_numpy()[shift_pos]
    employee_ids = shifts.employee_id.to_numpy()[shift_pos]
    role_ids = shifts.employee_role_id.to_numpy()[shift_pos]
    start_hour = np.select(
        [roles == "cashier", roles == "stock_associate", roles == "supervisor", roles == "manager"],
        [12, 6, 18, 18], default=22,
    ).astype(np.int64)
    offsets_ns = rng.integers(0, 7 * 60 * 60 * 1_000_000_000, size=n, dtype=np.int64)
    timestamps_ns = days.values.astype("datetime64[ns]").astype(np.int64)[day_pos]
    timestamps_ns += start_hour * 60 * 60 * 1_000_000_000 + offsets_ns
    # A transfer receipt follows its outbound leg and remains inside its shift.
    for out_idx, in_idx in transfer_pairs:
        timestamps_ns[in_idx] = timestamps_ns[out_idx] + 60 * 1_000_000_000

    employee_by_id = master.employees.set_index("employee_id")
    role_by_employee = master.employee_roles.set_index("employee_id").employee_role_id
    movement_ids = np.array([f"MOV-2026-{i:08d}" for i in range(1, n + 1)], dtype=object)
    movements = pd.DataFrame({
        "movement_id": movement_ids,
        "timestamp": pd.to_datetime(timestamps_ns),
        "store_id": [f"STORE-{x:06d}" for x in store_num],
        "sku_id": [f"SKU-{x:06d}" for x in sku_num],
        "movement_type": movement_type,
        "qty_change": qty_change,
        "employee_id": employee_ids,
        "employee_role_id": role_ids,
        "shift_id": shift_ids,
        "approval_code": approval_code,
        "reason_code": reason_code,
        "transfer_id": transfer_ids,
        "related_store_id": related_store_ids,
    }).sort_values(["timestamp", "movement_id"], kind="mergesort").reset_index(drop=True)

    # Link a subset of real positive Return events to unique earlier Sale events.
    # Unmatched Return events are ordinary receipts, never orphan return records.
    return_rows = movements.index[movements.movement_type.eq("Return")]
    sale_rows = movements.index[movements.movement_type.eq("Sale")]
    returns = pd.DataFrame(columns=["return_id", "movement_id", "original_sale_id", "store_id",
        "sku_id", "employee_id", "return_timestamp", "return_qty", "reason_code", "status"])
    if len(return_rows) and len(sale_rows):
        return_candidates = movements.loc[return_rows, ["movement_id", "store_id", "sku_id", "timestamp"]].copy()
        return_candidates["_return_row"] = return_candidates.index
        sale_candidates = movements.loc[sale_rows, ["movement_id", "store_id", "sku_id", "timestamp", "qty_change"]].copy()
        sale_candidates = sale_candidates.rename(columns={"movement_id": "original_sale_id", "timestamp": "sale_timestamp", "qty_change": "sale_qty"})
        pairs = return_candidates.merge(sale_candidates, on=["store_id", "sku_id"], how="inner")
        pairs["time_distance"] = (pairs.timestamp - pairs.sale_timestamp).abs()
        pairs = pairs.sort_values(["_return_row", "time_distance", "sale_timestamp"], kind="mergesort")
        pairs = pairs.drop_duplicates("_return_row").drop_duplicates("original_sale_id")
        linked_returns = set(pairs._return_row.astype(int))
        orphan_rows = [idx for idx in return_rows if int(idx) not in linked_returns]
        if orphan_rows:
            movements.loc[orphan_rows, "movement_type"] = "Receipt"
            orphan_dates = pd.to_datetime(movements.loc[orphan_rows, "timestamp"]).dt.normalize()
            orphan_store = movements.loc[orphan_rows, "store_id"].str[-6:].astype(int).to_numpy()
            orphan_day = (orphan_dates - days[0]).dt.days.to_numpy()
            orphan_shift_pos = ((orphan_store - 1) * day_count + orphan_day) * len(role_order) + role_index["stock_associate"]
            movements.loc[orphan_rows, "shift_id"] = shifts.shift_id.to_numpy()[orphan_shift_pos]
            movements.loc[orphan_rows, "employee_id"] = shifts.employee_id.to_numpy()[orphan_shift_pos]
            movements.loc[orphan_rows, "employee_role_id"] = shifts.employee_role_id.to_numpy()[orphan_shift_pos]
            orphan_offsets = rng.integers(0, 7 * 60 * 60 * 1_000_000_000, size=len(orphan_rows), dtype=np.int64)
            movements.loc[orphan_rows, "timestamp"] = orphan_dates.to_numpy() + pd.to_timedelta(
                6 * 60 * 60 * 1_000_000_000 + orphan_offsets, unit="ns"
            )
            movements.loc[orphan_rows, "reason_code"] = ""
            movements.loc[orphan_rows, "approval_code"] = ""
        if len(pairs):
            matched_rows = pairs._return_row.to_numpy(dtype=int)
            matched_store = pairs.store_id.str[-6:].astype(int).to_numpy()
            matched_day = (pd.to_datetime(pairs.sale_timestamp).dt.normalize() - days[0]).dt.days.to_numpy()
            matched_shift_pos = ((matched_store - 1) * day_count + matched_day) * len(role_order) + role_index["cashier"]
            movements.loc[matched_rows, "shift_id"] = shifts.shift_id.to_numpy()[matched_shift_pos]
            movements.loc[matched_rows, "employee_id"] = shifts.employee_id.to_numpy()[matched_shift_pos]
            movements.loc[matched_rows, "employee_role_id"] = shifts.employee_role_id.to_numpy()[matched_shift_pos]
            movements.loc[pairs._return_row.to_numpy(dtype=int), "timestamp"] = pd.to_datetime(
                pairs.sale_timestamp.to_numpy() + pd.Timedelta(minutes=1)
            )
            sale_quantity = pairs.sale_qty.abs().to_numpy(dtype=np.int64)
            movements.loc[pairs._return_row.to_numpy(dtype=int), "qty_change"] = np.minimum(
                movements.loc[pairs._return_row.to_numpy(dtype=int), "qty_change"].to_numpy(dtype=np.int64),
                sale_quantity,
            )
            returns = pd.DataFrame({
        "return_id": [f"RET-2026-{i:08d}" for i in range(1, len(pairs) + 1)],
                "movement_id": movements.loc[pairs._return_row.to_numpy(dtype=int), "movement_id"].to_numpy(),
                "original_sale_id": pairs.original_sale_id.to_numpy(),
                "store_id": pairs.store_id.to_numpy(), "sku_id": pairs.sku_id.to_numpy(),
                "employee_id": movements.loc[pairs._return_row.to_numpy(dtype=int), "employee_id"].to_numpy(),
                "return_timestamp": movements.loc[pairs._return_row.to_numpy(dtype=int), "timestamp"].to_numpy(),
                "return_qty": movements.loc[pairs._return_row.to_numpy(dtype=int), "qty_change"].to_numpy(dtype=np.int64),
                "reason_code": "CUSTOMER_RETURN", "status": "Accepted",
            })
    movements = movements.sort_values(["timestamp", "movement_id"], kind="mergesort").reset_index(drop=True)

    # Calculate event-time balances from source ledger values.
    opening = master.opening_balances.set_index(["store_id", "sku_id"]).opening_qty
    ledger = movements[["movement_id", "store_id", "sku_id", "timestamp", "qty_change"]].copy()
    ledger["running_net"] = ledger.groupby(["store_id", "sku_id"], sort=False).qty_change.cumsum()
    event_keys = pd.MultiIndex.from_arrays([ledger.store_id, ledger.sku_id])
    ledger["system_qty"] = ledger.running_net.to_numpy(dtype=np.int64) + opening.reindex(event_keys).to_numpy(dtype=np.int64)

    count_n = config.cycle_count_count
    count_pos = rng.integers(0, n, size=count_n, dtype=np.int64)
    chosen = ledger.iloc[count_pos].reset_index(drop=True)
    physical_delta = rng.choice(np.array([-2, -1, 0, 0, 0, 0, 0, 0, 1, 2]), size=count_n)
    counted = np.maximum(0, chosen.system_qty.to_numpy(dtype=np.int64) + physical_delta)
    cycle_counts = pd.DataFrame({
        "cycle_count_id": [f"CNT-2026-{i:06d}" for i in range(1, count_n + 1)],
        "movement_id": chosen.movement_id.to_numpy(),
        "timestamp": chosen.timestamp.to_numpy(),
        "store_id": chosen.store_id.to_numpy(), "sku_id": chosen.sku_id.to_numpy(),
        "system_qty": chosen.system_qty.to_numpy(dtype=np.int64),
        "counted_qty": counted,
        "variance_qty": counted - chosen.system_qty.to_numpy(dtype=np.int64),
        "location_type": "Store",
    }).sort_values(["timestamp", "cycle_count_id"], kind="mergesort").reset_index(drop=True)
    return TransactionData(movements=movements, cycle_counts=cycle_counts, returns=returns)
