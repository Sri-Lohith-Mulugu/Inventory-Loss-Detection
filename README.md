#  Inventory Loss Detection

## Overview

This repository contains a deterministic, synthetic inventory-event pipeline for demonstrating inventory-loss anomaly detection. It creates operational records, preserves planted changes in an event history, detects suspicious events from their evidence without consulting labels, and evaluates those detections against a separate ground-truth table.

The official full profile generates 100 stores, 5,000 SKUs, 1,000 employees, 500 promotions, 500,000 opening-balance pairs, 1,000,000 inventory movements, and 20,000 cycle counts. To ensure every movement has a same-store, authorized, in-window shift, the shift calendar contains one shift for each store, date, and role (182,500 rows for the 2026 calendar year). `starter` uses 10 stores, 100 SKUs, 100 employees, 50 promotions, 1,000 opening-balance pairs, 10,000 movements, and 500 counts.

## Architecture

```text
config.py -> master_data.py -> transactions.py -> anomalies.py
          -> detector.py -> evaluation.py -> validate.py -> main.py
```

- `config.py` holds the fixed seed, date and volume settings, CLI, and paths.
- `master_data.py` creates Store, SKU, Employee, EmployeeRole assignments, Shift, Promotion, and OpeningBalances.
- `transactions.py` generates signed, chronological movements, paired transfers, linked legitimate returns, and physical counts.
- `anomalies.py` mutates only selected records, records before/after snapshots, and writes separate labels.
- `detector.py` applies evidence rules and has no GroundTruth input.
- `evaluation.py` calculates category-level and micro-aggregated confusion counts, precision, recall, and F1.
- `validate.py` checks IDs, relationships, authorization, event time, lifecycle enums, arithmetic, anomaly evidence, and output label separation.
- `main.py` coordinates the pipeline and exports artifacts.

## Setup

Python 3.10 or newer is required. Runtime dependencies and the required transitive packages are pinned exactly in `requirements.txt`; the clean-environment verification was run on Python 3.12.6.

### Windows PowerShell

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### macOS or Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Run and verify

```bash
# Full 1M movement dataset; includes an in-process second generation check.
python main.py --scale full

# Faster generation, validation, exports, and repeatability check.
python main.py --scale starter

# Select a seed and output directory.
python main.py --seed 20261022 --scale starter --output-dir ./custom_out

# Full-scale isolated two-run SHA-256 comparison.
python verify_reproducibility.py

# Unit and integration checks.
python -m unittest discover -s tests -v

# Load schema.sql and all 16 SQL seeds into local SQLite, checking constraints,
# foreign keys, integrity, and row counts against the CSV outputs.
python verify_sql_seed_load.py --output-dir output
```

The CLI defaults to seed `20261022`, `full` scale, and `output/`. Dataset tables are exported as UTF-8 CSV, JSON arrays (`orient='records'`), and deterministic PostgreSQL `INSERT` scripts. The pipeline also writes `validation_summary.json`, `source_manifest.json`, and `sha256_manifest.csv`. A critical validation failure raises an exception and withholds the dataset exports.

## Output tables

The ten assessment core entities are Store, SKU, InventoryMovement, CycleCount, EmployeeRole, Shift, Promotion, Return, ShrinkageAlert, and Investigation. Employee is a supporting actor table. OpeningBalances, GroundTruth, EventHistory, Detection, and EvaluationMetrics are supporting tables. Return is a separate entity linked to its Return movement and original sale; it is not represented only as a movement type.

Primary-key formats are `STORE-000001`, `SKU-000001`, `EMP-0000001`, `ER-00000001` (EmployeeRole assignment), `SHIFT-00000001`, `PROMO-000001`, `MOV-2026-00000001`, `CNT-2026-000001`, `RET-2026-00000001`, `ANOM-000001`, `HIST-00000001`, `DET-00000001`, `SHR-2026-000001`, and `INV-2026-000001`. The assessment explicitly specifies Store, SKU, Movement, CycleCount, Alert, and Investigation formats; EmployeeRole and Return suffix widths are project conventions. See [data_dictionary.md](data_dictionary.md) for all field rules and [schema.sql](schema.sql) for PostgreSQL DDL.

`verify_sql_seed_load.py` executes the DDL and all 16 SQL seed files in Python's local SQLite runtime, with the PostgreSQL regex operator translated to SQLite `REGEXP`. It enforces the schema constraints and verifies foreign keys and loaded row counts. This is a local SQL smoke test, not a PostgreSQL server integration test.

## Baseline inventory and anomaly workflow

For each store/SKU, the clean ledger enforces:

```text
opening_qty + sum(signed qty_change) = expected ending quantity
```

Cycle-count system quantities are derived from the ledger at the referenced event time. Counted quantities include small, bounded physical differences to represent benign variance. Returns link to an earlier sale and cannot exceed its sold quantity. Damage movements are separately identified and have an operational reason. Clean transfers contain a balanced outbound/inbound pair with a shared transfer ID.

Four cases are planted after baseline generation:

1. **Wrong_Store_Transfer:** the outbound transfer points to a different existing store than the paired receipt.
2. **No_Reason_Adjustment:** a targeted cluster of negative adjustments has no approval or reason metadata.
3. **Time_Travel_Count:** a count precedes its referenced 10,000-unit receipt and carries the resulting negative variance.
4. **Double_Shrinkage_Transfer:** both endpoints of a paired transfer are changed into shrinkage postings.

`ground_truth` contains labels and typed target references separately from movement features. `event_history` stores before/after JSON for injected changes and investigation lifecycle states. The detector uses transfer-pair consistency, adjustment metadata, count/movement chronology, and dual-endpoint shrinkage evidence; it does not receive the ground-truth table. Investigations are derived one-to-one from detector alerts, start in `Open`, and follow deterministic evidence-based transitions through `In_Progress` and (for the generated closed case) `Closed`. These are synthetic workflow examples, not claims of human review.

Detector risk scores are derived from the evidence loss magnitude with the documented monotonic rule `0.5 + 0.5 * loss_qty / (loss_qty + 10)`, rounded to eight decimal places. Severity is separately derived from deterministic magnitude bands; neither value is randomly assigned.

## Actor assumptions

The assessment names Customer, store/warehouse operator, merchandising/e-commerce analyst, and operations manager as actors but does not specify exact UI or process interactions. These documented workflow assumptions are not additional required database entities: customer activity appears through sales/return events; operators record movements and counts under an authorized role and shift; analysts review SKU and promotion patterns; managers review/approve adjustments and own alert investigations. No Customer or Warehouse core entity is introduced solely from the actor list.

## Evaluation and limits

`evaluation_metrics` reports TP, FP, TN, FN, precision, recall, and F1 for each injected category and an overall micro aggregate. These are measurements against deterministic synthetic cases, not estimates of production fraud performance. No ML model, real retailer feed, live database deployment, calibrated threshold study, or generalization claim is included. Category-aware SKU prices/opening quantities and weighted event workloads create varied synthetic operating patterns; they are assumptions, not retailer-calibrated distributions. Promotion records are generated and validated but are not applied to sale prices because the assessment does not define promotion priority or sale-price semantics.

## Reproducibility and provenance

All random streams use the configured seed with isolated offsets. Artifact serialization omits run-time timestamps. `verify_reproducibility.py` generates two full-scale runs in separate temporary directories and compares SHA-256 digests for every CSV, JSON, and SQL artifact, including the validation report and manifests. Reproduction is expected under the same Python/dependency environment; `source_manifest.json` records package versions because Faker and numerical-library releases can change generated bytes.

The generator is synthetic-only and does not ingest external datasets. `source_manifest.json` records that provenance and the configured run. No external data is used, so conditional external-source metadata is not applicable. The data dictionary uses project-domain descriptions rather than external records.

## Assessment and submission notes

The supplied Guidelines request an executable project, visible assumptions and trade-offs, prompt history, honest AI-use disclosure, and a public GitHub repository with meaningful development history. The FAQ permits AI tools, does not mandate an automated test suite, and expects the candidate to understand and validate the work. `PROMPT_HISTORY.md` records AI-assisted development and this repository's verification evidence; the candidate must review and be able to explain the implementation personally.

The assessment email PDF supplied for this project states that it was sent on November 11, 2026 at 3:17 PM IST and gives a deadline of November 14, 2026 at 3:11:11 PM IST. Those two displayed times differ by 5 minutes 49 seconds from exactly 72 hours, so the stated deadline is transcribed as provided and the discrepancy is not silently reconciled. The email also says not to share or publish assessment content, problem statements, or interview questions. Do not include the official statement, PDFs, or email in any public repository; publish only authorized project work and review all files for secrets and personal/sensitive information.

### Candidate ownership and AI collaboration disclosure

OpenAI ChatGPT/Codex assisted with requirement decomposition, architecture iteration, Python and SQL implementation, debugging, documentation, and test authoring. The work used staged prompts: first define the pipeline and data contract, then implement bounded modules, then audit observed behavior and address evidence-backed gaps. 

During this continuation, observed defects included nullable empty strings being serialized as SQL empty strings (causing foreign-key failures), incomplete ID-format checks, and stale field/lifecycle documentation. The implementation was corrected and then checked through a clean temporary environment, starter execution, unit/integration tests, full-scale generation, and SQL seed loading. The candidate remains responsible for personally reviewing and understanding the submitted code, deciding whether to adopt it, and accurately describing any independent work ; see [PROMPT_HISTORY.md](PROMPT_HISTORY.md) for the collaboration record.

