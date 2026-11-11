"""Load the project DDL and all generated SQL seeds into local SQLite.

SQLite is used as a no-server SQL smoke-test runtime. The schema is PostgreSQL
DDL; the one PostgreSQL-only operator used by this schema (``~`` for regex
checks) is translated to SQLite's REGEXP operator, backed by Python ``re``.
All table definitions, constraints, inserts, and foreign keys are otherwise
executed by SQLite. This does not replace a PostgreSQL integration test.
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
from pathlib import Path


TABLE_FILES = {
    "stores": "Store",
    "skus": "SKU",
    "employees": "Employee",
    "employee_roles": "EmployeeRole",
    "shifts": "Shift",
    "promotions": "Promotion",
    "opening_balances": "OpeningBalances",
    "inventory_movements": "InventoryMovement",
    "returns": "ReturnEntity",
    "cycle_counts": "CycleCount",
    "ground_truth": "GroundTruth",
    "detections": "Detection",
    "shrinkage_alerts": "ShrinkageAlert",
    "investigations": "Investigation",
    "event_history": "EventHistory",
    "evaluation_metrics": "EvaluationMetrics",
}


def _regex(pattern: str | None, value: str | None) -> int:
    if pattern is None or value is None:
        return 0
    return int(re.search(pattern, value) is not None)


def _execute_statements(connection: sqlite3.Connection, path: Path) -> None:
    """Execute semicolon-terminated SQL statements without reading huge files at once."""
    statement = ""
    with path.open("r", encoding="utf-8", newline="") as stream:
        for line in stream:
            if not statement and line.lstrip().startswith("--"):
                continue
            statement += line
            if sqlite3.complete_statement(statement):
                sql = statement.strip()
                statement = ""
                if sql:
                    try:
                        connection.execute(sql)
                    except sqlite3.DatabaseError as exc:
                        raise RuntimeError(
                            f"SQL execution failed in {path.name}: {exc}; "
                            f"statement prefix={sql[:180]!r}"
                        ) from exc
    if statement.strip():
        raise ValueError(f"Incomplete SQL statement at end of {path.name}")


def _csv_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return sum(1 for _ in csv.DictReader(stream))


def validate_sql_load(output_dir: Path, schema_path: Path) -> dict[str, int]:
    sql_paths = {name: output_dir / f"{name}.sql" for name in TABLE_FILES}
    missing = [str(path) for path in sql_paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing expected SQL seed files: {missing}")
    extra = sorted(path.name for path in output_dir.glob("*.sql")
                   if path.name not in {f"{name}.sql" for name in TABLE_FILES})
    if extra:
        raise ValueError(f"Unexpected SQL seed files in {output_dir}: {extra}")

    ddl = schema_path.read_text(encoding="utf-8")
    # Preserve the source constraint semantics while adapting only its regex
    # operator to the local SQLite runtime.
    sqlite_ddl = re.sub(r"\s~\s", " REGEXP ", ddl)
    connection = sqlite3.connect(":memory:")
    connection.create_function("regexp", 2, _regex, deterministic=True)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(sqlite_ddl)
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("SQLite foreign-key enforcement could not be enabled")

    counts: dict[str, int] = {}
    try:
        for file_name, table_name in TABLE_FILES.items():
            _execute_statements(connection, sql_paths[file_name])
            loaded = int(connection.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0])
            csv_path = output_dir / f"{file_name}.csv"
            if csv_path.is_file():
                expected = _csv_rows(csv_path)
                if loaded != expected:
                    raise AssertionError(
                        f"{table_name}: SQL loaded {loaded} rows, CSV contains {expected}"
                    )
            counts[table_name] = loaded

        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise AssertionError(f"Foreign-key violations after load: {violations[:10]}")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise AssertionError(f"SQLite integrity_check failed: {integrity}")
    finally:
        connection.close()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--schema", type=Path, default=Path(__file__).with_name("schema.sql"))
    args = parser.parse_args()
    counts = validate_sql_load(args.output_dir, args.schema)
    print(f"SQL LOAD VALIDATION: PASS ({len(counts)} seed files)")
    for table, row_count in counts.items():
        print(f"{table}: {row_count:,} rows")
    print("SQLite foreign_key_check: PASS")
    print("SQLite integrity_check: PASS")
    print("PostgreSQL regex constraints were translated to SQLite REGEXP for this local smoke test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
