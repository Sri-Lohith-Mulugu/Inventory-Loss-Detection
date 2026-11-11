from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from faker import Faker

from config import PipelineConfig, DEFAULT_CONFIG


@dataclass(frozen=True)
class MasterData:
    stores: pd.DataFrame
    skus: pd.DataFrame
    employees: pd.DataFrame
    employee_roles: pd.DataFrame
    shifts: pd.DataFrame
    promotions: pd.DataFrame
    opening_balances: pd.DataFrame


def _faker_for_seed(seed: int) -> Faker:
    """Return an isolated Faker instance so generation does not depend on global state."""
    fake = Faker()
    fake.seed_instance(seed)
    return fake


def generate_stores(config: PipelineConfig = DEFAULT_CONFIG) -> pd.DataFrame:
    rng = np.random.default_rng(config.seed)
    fake = _faker_for_seed(config.seed)

    regions = ("Northeast", "Southeast", "Midwest", "Southwest", "West")
    rows = []

    for i in range(1, config.store_count + 1):
        rows.append(
            {
                "store_id": f"STORE-{i:06d}",
                "store_name": f"{fake.city()} Market {i:03d}",
                "city": fake.city(),
                "region": regions[(i - 1) % len(regions)],
                "opened_date": str(fake.date_between(start_date=date(2000, 1, 1), end_date=date(2024, 12, 31))),
                "latitude": round(float(rng.uniform(25.0, 49.0)), 6),
                "longitude": round(float(rng.uniform(-124.0, -67.0)), 6),
            }
        )

    return pd.DataFrame(rows)


def generate_skus(config: PipelineConfig = DEFAULT_CONFIG) -> pd.DataFrame:
    rng = np.random.default_rng(config.seed + 1)
    fake = _faker_for_seed(config.seed + 1)

    categories = np.array(("Grocery", "Household", "Electronics", "Apparel", "Health", "Beauty"), dtype=object)
    category_weights = np.array((0.34, 0.18, 0.10, 0.13, 0.14, 0.11), dtype=float)
    category = rng.choice(categories, size=config.sku_count, p=category_weights)
    cost_medians = {"Grocery": 5.0, "Household": 18.0, "Electronics": 120.0,
                    "Apparel": 24.0, "Health": 12.0, "Beauty": 9.0}
    cost_sigma = {"Grocery": 0.70, "Household": 0.75, "Electronics": 0.85,
                  "Apparel": 0.80, "Health": 0.65, "Beauty": 0.65}
    markup_ranges = {"Grocery": (1.25, 1.85), "Household": (1.35, 2.10),
                     "Electronics": (1.12, 1.45), "Apparel": (1.70, 2.60),
                     "Health": (1.35, 2.00), "Beauty": (1.45, 2.10)}
    rows = []

    for i in range(1, config.sku_count + 1):
        selected_category = str(category[i - 1])
        unit_cost = round(float(rng.lognormal(
            mean=np.log(cost_medians[selected_category]), sigma=cost_sigma[selected_category]
        )), 2)
        markup = float(rng.uniform(*markup_ranges[selected_category]))
        rows.append(
            {
                "sku_id": f"SKU-{i:06d}",
                "sku_name": fake.word().title() + f" {i:04d}",
                "category": selected_category,
                "unit_cost": unit_cost,
                "unit_price": round(unit_cost * markup, 2),
            }
        )

    return pd.DataFrame(rows)


def generate_employees(config: PipelineConfig = DEFAULT_CONFIG) -> pd.DataFrame:
    rng = np.random.default_rng(config.seed + 2)
    fake = _faker_for_seed(config.seed + 2)

    roles = ("cashier", "stock_associate", "supervisor", "manager", "auditor")
    dates = pd.date_range(config.start_date, config.end_date, freq="D")

    rows = []
    for i in range(1, config.employee_count + 1):
        hire_date = pd.Timestamp("2020-01-01") + pd.Timedelta(days=int(rng.integers(0, 2192)))
        rows.append(
            {
                "employee_id": f"EMP-{i:07d}",
                "store_id": f"STORE-{((i - 1) % config.store_count) + 1:06d}",
                "employee_name": fake.name(),
                "employee_role": roles[((i - 1) // config.store_count) % len(roles)],
                "hire_date": hire_date.strftime("%Y-%m-%d"),
            }
        )

    return pd.DataFrame(rows)


def generate_employee_roles(
    employees: pd.DataFrame, config: PipelineConfig = DEFAULT_CONFIG
) -> pd.DataFrame:
    """Represent employee role assignments as effective-dated core records."""
    effective_end = (pd.Timestamp(config.end_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    return pd.DataFrame({
        "employee_role_id": [f"ER-{i:08d}" for i in range(1, len(employees) + 1)],
        "employee_id": employees.employee_id.to_numpy(),
        "role_name": employees.employee_role.to_numpy(),
        "effective_from": config.start_date,
        # Include the end of the final overnight shift that starts on end_date.
        "effective_to": effective_end,
        "status": "Active",
    })


def generate_shifts(
    employees: pd.DataFrame, employee_roles: pd.DataFrame,
    config: PipelineConfig = DEFAULT_CONFIG,
) -> pd.DataFrame:
    """Create store/day/role shifts so each movement can link to an authorized shift."""
    roles = ("cashier", "stock_associate", "supervisor", "manager", "auditor")
    times = {"cashier": ("Midday", 12), "stock_associate": ("Opening", 6),
             "supervisor": ("Closing", 18), "manager": ("Closing", 18),
             "auditor": ("Overnight", 22)}
    days = pd.date_range(config.start_date, config.end_date, freq="D")
    employee_lookup = employees.sort_values("employee_id").drop_duplicates(
        ["store_id", "employee_role"], keep="first"
    )
    employee_by_store_role = {
        (int(row.store_id[-6:]), row.employee_role): row.employee_id
        for row in employee_lookup.itertuples(index=False)
    }
    role_id_by_employee = employee_roles.set_index("employee_id").employee_role_id.to_dict()
    store_nums = np.repeat(np.arange(1, config.store_count + 1), len(days) * len(roles))
    role_names = np.tile(np.asarray(roles, dtype=object), config.store_count * len(days))
    day_values = np.tile(np.repeat(days.to_numpy(), len(roles)), config.store_count)
    employee_ids = np.fromiter(
        (employee_by_store_role[(int(store), role)] for store, role in zip(store_nums, role_names)),
        dtype=object, count=len(store_nums),
    )
    shift_hours = np.fromiter((times[role][1] for role in role_names), dtype=np.int8, count=len(role_names))
    starts = pd.to_datetime(day_values).normalize() + pd.to_timedelta(shift_hours, unit="h")
    shifts = pd.DataFrame({
        "store_id": [f"STORE-{value:06d}" for value in store_nums],
        "employee_id": employee_ids,
        "employee_role_id": [role_id_by_employee[value] for value in employee_ids],
        "role_name": role_names,
        "shift_date": pd.to_datetime(day_values).strftime("%Y-%m-%d"),
        "shift_type": [times[role][0] for role in role_names],
        "shift_start": starts,
        "shift_end": starts + pd.Timedelta(hours=8),
    })
    shifts.insert(0, "shift_id", [f"SHIFT-{i:08d}" for i in range(1, len(shifts) + 1)])
    return shifts


def generate_promotions(config: PipelineConfig = DEFAULT_CONFIG) -> pd.DataFrame:
    """Generate deterministic SKU promotions with bounded date intervals."""
    rng = np.random.default_rng(config.seed + 5)
    day_values = pd.date_range(config.start_date, config.end_date, freq="D")
    start_pos = rng.integers(0, len(day_values), size=config.promotion_count)
    duration = rng.integers(1, 31, size=config.promotion_count)
    end_pos = np.minimum(start_pos + duration, len(day_values) - 1)
    sku_num = rng.integers(1, config.sku_count + 1, size=config.promotion_count)
    discount_rates = np.round(rng.uniform(0.05, 0.50, size=config.promotion_count), 4)
    return pd.DataFrame({
        "promo_id": [f"PROMO-{i:06d}" for i in range(1, config.promotion_count + 1)],
        "sku_id": [f"SKU-{value:06d}" for value in sku_num],
        "discount_rate": discount_rates,
        "start_date": day_values[start_pos].strftime("%Y-%m-%d"),
        "end_date": day_values[end_pos].strftime("%Y-%m-%d"),
    })


def generate_opening_balances(
    config: PipelineConfig = DEFAULT_CONFIG, skus: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Create one nonnegative opening quantity for every store–SKU pair."""
    rng = np.random.default_rng(config.seed + 3)

    store_ids = np.arange(1, config.store_count + 1, dtype=np.int32)
    sku_ids = np.arange(1, config.sku_count + 1, dtype=np.int32)

    store_grid, sku_grid = np.meshgrid(store_ids, sku_ids, indexing="ij")
    if skus is None:
        quantities = rng.integers(
            config.opening_qty_min, config.opening_qty_max + 1,
            size=store_grid.size, dtype=np.int32,
        )
    else:
        sku_categories = skus.sort_values("sku_id").category.to_numpy()
        high_velocity = {"Grocery", "Household"}
        low_velocity = {"Electronics"}
        category_max = np.array([
            min(config.opening_qty_max,
                max(config.opening_qty_min, int(config.opening_qty_max * (
                    1.0 if category in high_velocity else 0.25 if category in low_velocity else 0.60
                ))))
            for category in sku_categories
        ], dtype=np.int32)
        per_sku_max = category_max[sku_grid.ravel() - 1]
        random_fraction = rng.beta(2.0, 2.5, size=store_grid.size)
        quantities = config.opening_qty_min + np.floor(
            random_fraction * (per_sku_max - config.opening_qty_min + 1)
        ).astype(np.int32)
        quantities = np.minimum(quantities, config.opening_qty_max)

    return pd.DataFrame(
        {
            "store_id": [f"STORE-{value:06d}" for value in store_grid.ravel()],
            "sku_id": [f"SKU-{value:06d}" for value in sku_grid.ravel()],
            "opening_qty": quantities,
        }
    )


def generate_master_data(config: PipelineConfig = DEFAULT_CONFIG) -> MasterData:
    """Generate every master table independently from configured seed offsets."""
    stores = generate_stores(config)
    skus = generate_skus(config)
    employees = generate_employees(config)
    employee_roles = generate_employee_roles(employees, config)
    return MasterData(
        stores=stores,
        skus=skus,
        employees=employees,
        employee_roles=employee_roles,
        shifts=generate_shifts(employees, employee_roles, config),
        promotions=generate_promotions(config),
        opening_balances=generate_opening_balances(config, skus),
    )


if __name__ == "__main__":
    config = DEFAULT_CONFIG
    config.output_dir.mkdir(parents=True, exist_ok=True)
    master = generate_master_data(config)

    master.stores.to_csv(config.stores_path, index=False)
    master.skus.to_csv(config.skus_path, index=False)
    master.employees.to_csv(config.employees_path, index=False)
    master.employee_roles.to_csv(config.output_dir / "employee_roles.csv", index=False)
    master.shifts.to_csv(config.shifts_path, index=False)
    master.promotions.to_csv(config.promotions_path, index=False)
    master.opening_balances.to_csv(config.opening_balances_path, index=False)
    print(f"Master data generated successfully in '{config.output_dir}'")
