# Data Dictionary

Types describe the CSV/JSON representation and PostgreSQL `schema.sql` types. All listed fields are required unless marked Optional. IDs are immutable strings; dates use `YYYY-MM-DD`; timestamps use ISO-8601. Quantities are whole inventory units. Every table has a primary key unless a composite key is identified.

## Core entities

### Store

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|---:|---|---|---|
| `store_id` | string | Yes | `STORE-` + 6 digits | PK | Exact format; resolves all store FKs. |
| `store_name` | string | Yes | — | No | Non-empty generated display name. |
| `city` | string | Yes | — | No | Non-empty. |
| `region` | enum string | Yes | Northeast, Southeast, Midwest, Southwest, West | No | Must be one of the listed regions. |
| `opened_date` | date | Yes | — | No | Must parse as a date and precede simulation start. |
| `latitude` | decimal(9,6) | Yes | Degrees | No | Between -90 and 90. |
| `longitude` | decimal(9,6) | Yes | Degrees | No | Between -180 and 180. |

### SKU

Categories are sampled from documented retail-style weights (Grocery 34%, Household 18%, Electronics 10%, Apparel 13%, Health 14%, Beauty 11%). Unit costs use category-specific log-normal parameters and markup ranges; these are synthetic assumptions for varied category pricing, not retailer benchmarks.

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|---:|---|---|---|
| `sku_id` | string | Yes | `SKU-` + 6 digits | PK | Exact format. |
| `sku_name` | string | Yes | — | No | Non-empty. |
| `category` | enum string | Yes | Grocery, Household, Electronics, Apparel, Health, Beauty | No | Listed category only. |
| `unit_cost` | decimal(12,2) | Yes | Currency units | No | Nonnegative. |
| `unit_price` | decimal(12,2) | Yes | Currency units | No | Nonnegative. |

### Employee (supporting actor table)

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|---:|---|---|---|
| `employee_id` | string | Yes | `EMP-` + 7 digits | PK | Exact format. |
| `store_id` | string | Yes | Store ID | No | FK to Store; employee home store matches generated movement store. |
| `employee_name` | string | Yes | Synthetic Faker name | No | Synthetic value only. |
| `employee_role` | enum string | Yes | cashier, stock_associate, supervisor, manager, auditor | No | Matches an EmployeeRole assignment. |
| `hire_date` | date | Yes | — | No | Must be on/before simulated movement dates. |

### EmployeeRole

An effective-dated employee role assignment. The current generator emits one active assignment per employee for the simulation period.

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|---:|---|---|---|
| `employee_role_id` | string | Yes | `ER-` + 8 digits | PK | Zero-padded assignment key; assessment does not prescribe this supporting-entity width. |
| `employee_id` | string | Yes | Employee ID | No | FK to Employee. |
| `role_name` | enum string | Yes | cashier, stock_associate, supervisor, manager, auditor | No | Role is effective on movement date. |
| `effective_from` | date | Yes | — | No | Must be on/before `effective_to`. |
| `effective_to` | date | Yes | — | No | Must be on/after `effective_from`. |
| `status` | enum string | Yes | Active, Inactive | No | Only Active assignments may authorize generated movement. |

### Shift

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|---:|---|---|---|
| `shift_id` | string | Yes | `SHIFT-` + 8 digits | PK | Exact format. |
| `store_id` | string | Yes | Store ID | No | FK to Store; matches assigned employee's store. |
| `employee_id` | string | Yes | Employee ID | No | FK to Employee and EmployeeRole assignment. |
| `employee_role_id` | string | Yes | EmployeeRole ID | No | Composite FK to role assignment for this employee. |
| `role_name` | enum string | Yes | Listed EmployeeRole values | No | Must match referenced role assignment. |
| `shift_date` | date | Yes | — | No | Local calendar date of shift start. |
| `shift_type` | enum string | Yes | Opening, Midday, Closing, Overnight | No | Listed type only. |
| `shift_start` | timestamp | Yes | Local timestamp | No | Earlier than `shift_end`. |
| `shift_end` | timestamp | Yes | Local timestamp | No | Movement timestamp must lie in interval `[start,end)`. |

### Promotion

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|---:|---|---|---|
| `promo_id` | string | Yes | `PROMO-` + 6 digits | PK | Exact format. |
| `sku_id` | string | Yes | SKU ID | No | FK to SKU. |
| `discount_rate` | decimal(5,4) | Yes | Fraction | No | Greater than 0 and at most 1. |
| `start_date` | date | Yes | — | No | Must be on/before `end_date`. |
| `end_date` | date | Yes | — | No | Must be on/after `start_date`; inclusive. |

### InventoryMovement

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|---:|---|---|---|
| `movement_id` | string | Yes | `MOV-2026-` + 8 digits | PK | Exact format. |
| `timestamp` | timestamp | Yes | Local event time | No | Falls within linked shift; ledger sorts by time and ID. |
| `store_id` | string | Yes | Store ID | No | FK to Store and composite FK to linked shift. |
| `sku_id` | string | Yes | SKU ID | No | FK to SKU. |
| `movement_type` | enum string | Yes | Receipt, Sale, Return, Adjustment, Damage, Transfer_Out, Transfer_In, Shrinkage | No | Sign convention below. |
| `qty_change` | integer | Yes | Inventory units | No | Nonzero; Receipt/Return/Transfer_In positive; Sale/Damage/Transfer_Out/Shrinkage negative; Adjustment either sign. |
| `employee_id` | string | Yes | Employee ID | No | FK to Employee, role assignment, and shift; employee authorized for store. |
| `employee_role_id` | string | Yes | EmployeeRole ID | No | FK to effective active role assignment. |
| `shift_id` | string | Yes | Shift ID | No | FK to shift for same employee, role and store; time inside shift. |
| `approval_code` | string | Optional | Approval reference | No | Required for baseline adjustments/damage; deliberately absent for targeted crime. |
| `reason_code` | string | Optional | Reason code | No | Required for baseline adjustments/damage; deliberately absent for targeted crime. |
| `transfer_id` | string | Optional | `TRF-` grouping token | No | Non-empty on exactly one outbound and one inbound clean leg; legs reconcile. |
| `related_store_id` | string | Optional | Store ID | No | FK to Store; transfer endpoint must match paired leg. |

### CycleCount

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|---:|---|---|---|
| `cycle_count_id` | string | Yes | `CNT-2026-` + 6 digits | PK | Exact required format. |
| `movement_id` | string | Yes | Movement ID | No | FK to ledger point; store/SKU match and baseline timestamp equals referenced movement. |
| `timestamp` | timestamp | Yes | Count time | No | Must not precede referenced event in clean data. |
| `store_id` | string | Yes | Store ID | No | FK and same store as referenced movement. |
| `sku_id` | string | Yes | SKU ID | No | FK and same SKU as referenced movement. |
| `system_qty` | integer | Yes | Inventory units | No | Derived from opening balance plus signed movements at reference point. |
| `counted_qty` | integer | Yes | Inventory units | No | Nonnegative physical count. |
| `variance_qty` | integer | Yes | Inventory units | No | Exactly `counted_qty - system_qty`. |
| `location_type` | enum string | Yes | Store, Warehouse | No | Listed location only; generator currently emits Store. |

### Return

The SQL table is named `ReturnEntity` to avoid SQL keyword ambiguity. Each row references one positive Return movement and one earlier sale.

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|---:|---|---|---|
| `return_id` | string | Yes | `RET-2026-` + 8 digits | PK | Zero-padded; assessment does not prescribe this supporting-entity width. |
| `movement_id` | string | Yes | Movement ID | Unique FK | Must reference a Return movement. |
| `original_sale_id` | string | Yes | Movement ID | No | FK to earlier Sale at same store/SKU; returned quantity cannot exceed sale quantity. |
| `store_id` | string | Yes | Store ID | No | FK and matches sale and return movement. |
| `sku_id` | string | Yes | SKU ID | No | FK and matches sale and return movement. |
| `employee_id` | string | Yes | Employee ID | No | FK to authorized return movement actor. |
| `return_timestamp` | timestamp | Yes | Return event time | No | Must follow original sale and equal linked movement time. |
| `return_qty` | integer | Yes | Inventory units | No | Positive and no greater than sold quantity. |
| `reason_code` | string | Yes | CUSTOMER_RETURN | No | Must be non-empty. |
| `status` | enum string | Yes | Accepted, Rejected, Reviewed | No | Current generator emits Accepted. |

### ShrinkageAlert

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|---:|---|---|---|
| `alert_id` | string | Yes | `SHR-2026-` + 6 digits | PK | Exact required format. |
| `detection_id` | string | Yes | Detection ID | Unique FK | Must resolve to independent detector output. |
| `timestamp` | timestamp | Yes | Detection time | No | Derived from detection evidence. |
| `entity_type` | enum string | Yes | InventoryMovement, CycleCount | No | Selects exactly one typed target FK. |
| `target_id` | string | Yes | Movement or count ID | No | Must match selected typed target FK. |
| `movement_id` | string | Conditional | Movement ID | FK | Required only for movement alerts; otherwise null. |
| `cycle_count_id` | string | Conditional | CycleCount ID | FK | Required only for count alerts; otherwise null. |
| `issue_type` | enum string | Yes | Four planted issue types | No | Detector reason category; not copied into baseline features. |
| `store_id` | string | Yes | Store ID | No | FK to Store and matches evidence. |
| `sku_id` | string | Yes | SKU ID | No | FK to SKU and matches evidence. |
| `loss_qty` | integer | Yes | Inventory units | No | Derived from event loss or count variance. |
| `risk_score` | decimal [0,1] | Yes | Unitless | No | `0.5 + 0.5 * loss_qty / (loss_qty + 10)`; rounded to 8 decimals. |
| `severity` | enum string | Yes | Low, Medium, High, Critical | No | Rule-derived from loss magnitude. |
| `alert_reason` | string | Yes | Evidence text | No | Non-empty explanation from detector evidence. |

### Investigation

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|---:|---|---|---|
| `investigation_id` | string | Yes | `INV-2026-` + 6 digits | PK | Exact required format. |
| `alert_id` | string | Yes | Alert ID | Unique FK | One initial investigation per alert in current design. |
| `status` | enum string | Yes | Open, In_Progress, Closed | No | Generated cases start Open; deterministic synthetic paths may transition Open → In_Progress → Closed. No direct Open → Closed transition. |
| `resolution_findings` | string | Yes | Evidence-backed text | No | Derived from linked alert evidence; a generated Closed state is a synthetic disposition, not a human investigation outcome. |

## Supporting tables

### OpeningBalances

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|---:|---|---|---|
| `store_id` | string | Yes | Store ID | Composite PK | FK to Store. |
| `sku_id` | string | Yes | SKU ID | Composite PK | FK to SKU. |
| `opening_qty` | integer | Yes | Inventory units | No | Nonnegative starting stock. |

### GroundTruth

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|---:|---|---|---|
| `anomaly_id` | string | Yes | `ANOM-` + 6 digits | PK | Unique label row. |
| `entity_type` | enum string | Yes | InventoryMovement, CycleCount | No | Determines typed target FK. |
| `target_id` | string | Yes | Movement or count ID | No | Equals exactly one typed target field. |
| `movement_id` | string | Conditional | Movement ID | FK | Populated only for movement anomaly. |
| `cycle_count_id` | string | Conditional | CycleCount ID | FK | Populated only for count anomaly. |
| `issue_type` | enum string | Yes | Wrong_Store_Transfer, No_Reason_Adjustment, Time_Travel_Count, Double_Shrinkage_Transfer | No | Label from documented injection rule. |
| `store_id` | string | Yes | Store ID | No | FK to Store. |
| `sku_id` | string | Yes | SKU ID | No | FK to SKU. |
| `details` | string | Yes | Human-readable evidence description | No | Describes planted mutation; excluded from detector inputs. |

### EventHistory

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|---:|---|---|---|
| `event_history_id` | string | Yes | `HIST-` + 8 digits | PK | Unique ordered history event. |
| `entity_type` | enum string | Yes | InventoryMovement, CycleCount, Investigation | No | Must match the one typed target FK. |
| `target_id` | string | Yes | Entity ID | No | Equals typed FK. |
| `movement_id` | string | Conditional | Movement ID | FK | One typed target only. |
| `cycle_count_id` | string | Conditional | CycleCount ID | FK | One typed target only. |
| `investigation_id` | string | Conditional | Investigation ID | FK | One typed target only. |
| `version` | integer | Yes | Positive sequence | No | Greater than zero. |
| `event_type` | enum string | Yes | ANOMALY_INJECTION, STATUS_INITIALIZED, STATUS_TRANSITION | No | Documents the state change. |
| `event_timestamp` | timestamp | Yes | Event time | No | Derived from affected source event. |
| `before_json` | JSON text | Optional | Prior state | No | Preserved for mutations; null for initial state. |
| `after_json` | JSON text | Yes | New state | No | Required snapshot. |
| `reason_code` | string | Yes | Injection/creation reason | No | Non-empty. |

### Detection

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|---:|---|---|---|
| `detection_id` | string | Yes | `DET-` + 8 digits | PK | Unique detector result. |
| `entity_type` | enum string | Yes | InventoryMovement, CycleCount | No | Must match typed target. |
| `target_id` | string | Yes | Movement or count ID | No | Equals one typed FK. |
| `store_id` | string | Yes | Store ID | No | FK and matches target. |
| `sku_id` | string | Yes | SKU ID | No | FK and matches target. |
| `issue_type` | enum string | Yes | Four detector categories | No | From evidence rule, never label input. |
| `detection_timestamp` | timestamp | Yes | Detection/evidence time | No | Derived from target. |
| `loss_qty` | integer | Yes | Inventory units | No | Nonnegative magnitude from target evidence. |
| `risk_score` | decimal [0,1] | Yes | Unitless | No | Derived with the documented monotonic loss-magnitude formula. |
| `severity` | enum string | Yes | Low, Medium, High, Critical | No | Derived by deterministic magnitude rule. |
| `evidence` | string | Yes | Explanation | No | Non-empty rule evidence. |
| `movement_id` | string | Conditional | Movement ID | FK | Set for InventoryMovement target. |
| `cycle_count_id` | string | Conditional | Count ID | FK | Set for CycleCount target. |

### EvaluationMetrics

| Field | Type | Required | Allowed values / units | Unique | Validation |
|---|---|---:|---|---|---|
| `issue_type` | enum string | Yes | Four categories, Overall_Micro | PK | One row per category and micro aggregate. |
| `candidate_population` | integer | Yes | Candidate records | No | Nonnegative denominator population. |
| `true_positive` | integer | Yes | Count | No | Nonnegative confusion count. |
| `false_positive` | integer | Yes | Count | No | Nonnegative confusion count. |
| `true_negative` | integer | Yes | Count | No | Nonnegative confusion count. |
| `false_negative` | integer | Yes | Count | No | Nonnegative confusion count. |
| `precision` | decimal | Yes | Ratio [0,1] | No | Zero when denominator is zero; otherwise TP/(TP+FP). |
| `recall` | decimal | Yes | Ratio [0,1] | No | Zero when denominator is zero; otherwise TP/(TP+FN). |
| `f1_score` | decimal | Yes | Ratio [0,1] | No | Harmonic mean of precision and recall, zero-safe. |

## Relationship, event and inventory rules

- `Store` 1:N `Employee`, `Shift`, `InventoryMovement`, `CycleCount`, `ShrinkageAlert`, and `GroundTruth`.
- `SKU` 1:N `InventoryMovement`, `CycleCount`, `Promotion`, `ShrinkageAlert`, and `GroundTruth`.
- `Store` N:M `SKU` through `OpeningBalances`, whose composite key stores the opening quantity.
- `Employee` 1:N `EmployeeRole`, `Shift`, and `InventoryMovement`; effective role assignment and shift link are carried on each movement.
- Each `InventoryMovement` has one employee, role assignment, and shift. The movement timestamp must fall inside that shift.
- A clean transfer is exactly one `Transfer_Out` and one `Transfer_In` with a shared `transfer_id`, matching SKU and quantity, reversed endpoint references, and dispatch no later than receipt.
- One `ReturnEntity` refers to one Return movement and one earlier Sale; returned quantity cannot exceed original quantity. Partial-return policy is one row per original sale in this generator.
- One CycleCount references one movement point; multiple counts may reference the same movement.
- Each Detection maps to exactly one typed movement or count target. Each alert references one Detection; each Investigation references one alert (current 1:1 choice).
- `GroundTruth` remains separate from Detection inputs. EventHistory stores mutation snapshots and investigation initialization/transitions without overwriting prior states.
- The allowed Investigation workflow is `Open -> In_Progress -> Closed`; `Open -> Closed` is not permitted. The generated initial Open state, subsequent review state, and synthetic evidence-based closure are deterministic scenario dispositions, not human investigation outcomes.
- EventHistory `version` increases consecutively per target; state-transition events must have before/after states matching the allowed workflow and the exported current Investigation status.
- Baseline stock is opening quantity plus signed movement sum. Cycle-count `system_qty` is derived from the ledger at its reference event; `variance_qty = counted_qty - system_qty`.
- Opening stock varies by SKU category: grocery/household categories have higher configured stock ceilings and electronics lower ceilings, subject to configured nonnegative inventory bounds.
- At least one movement is generated for every Store/SKU pair; additional event workload is sampled from seeded store-size and SKU-category/popularity weights, so movement frequency varies while retaining full opening-pair coverage.
- Promotion overlap/priority is not defined by the official statement; the current generator does not apply discounts to sales.
