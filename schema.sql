-- PostgreSQL DDL for the ten YottaFlex core entities and supporting audit data.
-- GroundTruth, Detection, history, employees, and OpeningBalances are supporting
-- structures. GroundTruth is never joined into detector feature inputs.

CREATE TABLE IF NOT EXISTS Store (
    store_id VARCHAR(13) PRIMARY KEY CHECK (store_id ~ '^STORE-[0-9]{6}$'),
    store_name VARCHAR(160) NOT NULL,
    city VARCHAR(100) NOT NULL,
    region VARCHAR(32) NOT NULL CHECK (region IN ('Northeast','Southeast','Midwest','Southwest','West')),
    opened_date DATE NOT NULL,
    latitude NUMERIC(9,6) NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude NUMERIC(9,6) NOT NULL CHECK (longitude BETWEEN -180 AND 180)
);

CREATE TABLE IF NOT EXISTS SKU (
    sku_id VARCHAR(10) PRIMARY KEY CHECK (sku_id ~ '^SKU-[0-9]{6}$'),
    sku_name VARCHAR(160) NOT NULL,
    category VARCHAR(32) NOT NULL CHECK (category IN ('Grocery','Household','Electronics','Apparel','Health','Beauty')),
    unit_cost NUMERIC(12,2) NOT NULL CHECK (unit_cost >= 0),
    unit_price NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0)
);

-- Employee is a supporting actor table; role assignments are the core EmployeeRole entity.
CREATE TABLE IF NOT EXISTS Employee (
    employee_id VARCHAR(11) PRIMARY KEY CHECK (employee_id ~ '^EMP-[0-9]{7}$'),
    store_id VARCHAR(13) NOT NULL REFERENCES Store(store_id),
    employee_name VARCHAR(160) NOT NULL,
    employee_role VARCHAR(32) NOT NULL CHECK (employee_role IN ('cashier','stock_associate','supervisor','manager','auditor')),
    hire_date DATE NOT NULL,
    UNIQUE (employee_id, store_id)
);

CREATE TABLE IF NOT EXISTS EmployeeRole (
    employee_role_id VARCHAR(11) PRIMARY KEY CHECK (employee_role_id ~ '^ER-[0-9]{8}$'),
    employee_id VARCHAR(11) NOT NULL REFERENCES Employee(employee_id),
    role_name VARCHAR(32) NOT NULL CHECK (role_name IN ('cashier','stock_associate','supervisor','manager','auditor')),
    effective_from DATE NOT NULL,
    effective_to DATE NOT NULL,
    status VARCHAR(12) NOT NULL CHECK (status IN ('Active','Inactive')),
    UNIQUE (employee_role_id, employee_id),
    CHECK (effective_to >= effective_from)
);

CREATE TABLE IF NOT EXISTS Shift (
    shift_id VARCHAR(16) PRIMARY KEY CHECK (shift_id ~ '^SHIFT-[0-9]{8}$'),
    store_id VARCHAR(13) NOT NULL REFERENCES Store(store_id),
    employee_id VARCHAR(11) NOT NULL REFERENCES Employee(employee_id),
    employee_role_id VARCHAR(11) NOT NULL,
    role_name VARCHAR(32) NOT NULL CHECK (role_name IN ('cashier','stock_associate','supervisor','manager','auditor')),
    shift_date DATE NOT NULL,
    shift_type VARCHAR(16) NOT NULL CHECK (shift_type IN ('Opening','Midday','Closing','Overnight')),
    shift_start TIMESTAMP NOT NULL,
    shift_end TIMESTAMP NOT NULL,
    UNIQUE (shift_id, employee_id, employee_role_id, store_id),
    FOREIGN KEY (employee_id, store_id) REFERENCES Employee(employee_id, store_id),
    FOREIGN KEY (employee_role_id, employee_id) REFERENCES EmployeeRole(employee_role_id, employee_id),
    CHECK (shift_end > shift_start)
);

CREATE TABLE IF NOT EXISTS Promotion (
    promo_id VARCHAR(16) PRIMARY KEY CHECK (promo_id ~ '^PROMO-[0-9]{6}$'),
    sku_id VARCHAR(10) NOT NULL REFERENCES SKU(sku_id),
    discount_rate NUMERIC(5,4) NOT NULL CHECK (discount_rate > 0 AND discount_rate <= 1),
    start_date DATE NOT NULL,
    end_date DATE NOT NULL CHECK (end_date >= start_date)
);

CREATE TABLE IF NOT EXISTS OpeningBalances (
    store_id VARCHAR(13) NOT NULL REFERENCES Store(store_id),
    sku_id VARCHAR(10) NOT NULL REFERENCES SKU(sku_id),
    opening_qty BIGINT NOT NULL CHECK (opening_qty >= 0),
    PRIMARY KEY (store_id, sku_id)
);

CREATE TABLE IF NOT EXISTS InventoryMovement (
    movement_id VARCHAR(20) PRIMARY KEY CHECK (movement_id ~ '^MOV-2026-[0-9]{8}$'),
    "timestamp" TIMESTAMP NOT NULL,
    store_id VARCHAR(13) NOT NULL REFERENCES Store(store_id),
    sku_id VARCHAR(10) NOT NULL REFERENCES SKU(sku_id),
    movement_type VARCHAR(20) NOT NULL CHECK (movement_type IN ('Receipt','Sale','Return','Adjustment','Damage','Transfer_Out','Transfer_In','Shrinkage')),
    qty_change BIGINT NOT NULL CHECK (qty_change <> 0),
    employee_id VARCHAR(11) NOT NULL,
    employee_role_id VARCHAR(11) NOT NULL,
    shift_id VARCHAR(16) NOT NULL,
    approval_code VARCHAR(40),
    reason_code VARCHAR(40),
    transfer_id VARCHAR(24),
    related_store_id VARCHAR(13) REFERENCES Store(store_id),
    FOREIGN KEY (employee_role_id, employee_id) REFERENCES EmployeeRole(employee_role_id, employee_id),
    FOREIGN KEY (shift_id, employee_id, employee_role_id, store_id)
        REFERENCES Shift(shift_id, employee_id, employee_role_id, store_id),
    CHECK ((movement_type IN ('Receipt','Return','Transfer_In') AND qty_change > 0)
        OR (movement_type IN ('Sale','Damage','Transfer_Out','Shrinkage') AND qty_change < 0)
        OR movement_type = 'Adjustment')
);

CREATE TABLE IF NOT EXISTS ReturnEntity (
    return_id VARCHAR(17) PRIMARY KEY CHECK (return_id ~ '^RET-2026-[0-9]{8}$'),
    movement_id VARCHAR(20) NOT NULL UNIQUE REFERENCES InventoryMovement(movement_id),
    original_sale_id VARCHAR(20) NOT NULL REFERENCES InventoryMovement(movement_id),
    store_id VARCHAR(13) NOT NULL REFERENCES Store(store_id),
    sku_id VARCHAR(10) NOT NULL REFERENCES SKU(sku_id),
    employee_id VARCHAR(11) NOT NULL REFERENCES Employee(employee_id),
    return_timestamp TIMESTAMP NOT NULL,
    return_qty BIGINT NOT NULL CHECK (return_qty > 0),
    reason_code VARCHAR(40) NOT NULL,
    status VARCHAR(16) NOT NULL CHECK (status IN ('Accepted','Rejected','Reviewed')),
    UNIQUE (original_sale_id)
);

CREATE TABLE IF NOT EXISTS CycleCount (
    cycle_count_id VARCHAR(19) PRIMARY KEY CHECK (cycle_count_id ~ '^CNT-2026-[0-9]{6}$'),
    movement_id VARCHAR(20) NOT NULL REFERENCES InventoryMovement(movement_id),
    "timestamp" TIMESTAMP NOT NULL,
    store_id VARCHAR(13) NOT NULL REFERENCES Store(store_id),
    sku_id VARCHAR(10) NOT NULL REFERENCES SKU(sku_id),
    system_qty BIGINT NOT NULL CHECK (system_qty >= 0),
    counted_qty BIGINT NOT NULL CHECK (counted_qty >= 0),
    variance_qty BIGINT NOT NULL,
    location_type VARCHAR(16) NOT NULL CHECK (location_type IN ('Store','Warehouse')),
    CHECK (variance_qty = counted_qty - system_qty)
);

CREATE TABLE IF NOT EXISTS Detection (
    detection_id VARCHAR(12) PRIMARY KEY CHECK (detection_id ~ '^DET-[0-9]{8}$'),
    entity_type VARCHAR(24) NOT NULL CHECK (entity_type IN ('InventoryMovement','CycleCount')),
    target_id VARCHAR(24) NOT NULL,
    movement_id VARCHAR(20) REFERENCES InventoryMovement(movement_id),
    cycle_count_id VARCHAR(19) REFERENCES CycleCount(cycle_count_id),
    issue_type VARCHAR(40) NOT NULL CHECK (issue_type IN ('Wrong_Store_Transfer','No_Reason_Adjustment','Time_Travel_Count','Double_Shrinkage_Transfer')),
    store_id VARCHAR(13) NOT NULL REFERENCES Store(store_id),
    sku_id VARCHAR(10) NOT NULL REFERENCES SKU(sku_id),
    detection_timestamp TIMESTAMP NOT NULL,
    loss_qty BIGINT NOT NULL CHECK (loss_qty >= 0),
    risk_score NUMERIC(9,8) NOT NULL CHECK (risk_score BETWEEN 0 AND 1),
    severity VARCHAR(16) NOT NULL CHECK (severity IN ('Low','Medium','High','Critical')),
    evidence TEXT NOT NULL,
    UNIQUE (entity_type, target_id, issue_type),
    CHECK ((movement_id IS NOT NULL AND cycle_count_id IS NULL AND entity_type = 'InventoryMovement' AND target_id = movement_id)
        OR (movement_id IS NULL AND cycle_count_id IS NOT NULL AND entity_type = 'CycleCount' AND target_id = cycle_count_id))
);

CREATE TABLE IF NOT EXISTS ShrinkageAlert (
    alert_id VARCHAR(18) PRIMARY KEY CHECK (alert_id ~ '^SHR-2026-[0-9]{6}$'),
    detection_id VARCHAR(12) NOT NULL UNIQUE REFERENCES Detection(detection_id),
    "timestamp" TIMESTAMP NOT NULL,
    entity_type VARCHAR(24) NOT NULL CHECK (entity_type IN ('InventoryMovement','CycleCount')),
    target_id VARCHAR(24) NOT NULL,
    movement_id VARCHAR(20) REFERENCES InventoryMovement(movement_id),
    cycle_count_id VARCHAR(19) REFERENCES CycleCount(cycle_count_id),
    issue_type VARCHAR(40) NOT NULL CHECK (issue_type IN ('Wrong_Store_Transfer','No_Reason_Adjustment','Time_Travel_Count','Double_Shrinkage_Transfer')),
    store_id VARCHAR(13) NOT NULL REFERENCES Store(store_id),
    sku_id VARCHAR(10) NOT NULL REFERENCES SKU(sku_id),
    loss_qty BIGINT NOT NULL,
    risk_score NUMERIC(9,8) NOT NULL CHECK (risk_score BETWEEN 0 AND 1),
    severity VARCHAR(16) NOT NULL CHECK (severity IN ('Low','Medium','High','Critical')),
    alert_reason TEXT NOT NULL,
    CHECK ((movement_id IS NOT NULL AND cycle_count_id IS NULL AND entity_type = 'InventoryMovement' AND target_id = movement_id)
        OR (movement_id IS NULL AND cycle_count_id IS NOT NULL AND entity_type = 'CycleCount' AND target_id = cycle_count_id))
);

CREATE TABLE IF NOT EXISTS Investigation (
    investigation_id VARCHAR(18) PRIMARY KEY CHECK (investigation_id ~ '^INV-2026-[0-9]{6}$'),
    alert_id VARCHAR(18) NOT NULL UNIQUE REFERENCES ShrinkageAlert(alert_id),
    status VARCHAR(16) NOT NULL CHECK (status IN ('Open','In_Progress','Closed')),
    resolution_findings TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS GroundTruth (
    anomaly_id VARCHAR(14) PRIMARY KEY CHECK (anomaly_id ~ '^ANOM-[0-9]{6}$'),
    entity_type VARCHAR(24) NOT NULL CHECK (entity_type IN ('InventoryMovement','CycleCount')),
    target_id VARCHAR(24) NOT NULL,
    movement_id VARCHAR(20) REFERENCES InventoryMovement(movement_id),
    cycle_count_id VARCHAR(19) REFERENCES CycleCount(cycle_count_id),
    issue_type VARCHAR(40) NOT NULL CHECK (issue_type IN ('Wrong_Store_Transfer','No_Reason_Adjustment','Time_Travel_Count','Double_Shrinkage_Transfer')),
    store_id VARCHAR(13) NOT NULL REFERENCES Store(store_id),
    sku_id VARCHAR(10) NOT NULL REFERENCES SKU(sku_id),
    details TEXT NOT NULL,
    UNIQUE (entity_type, target_id, issue_type),
    CHECK ((movement_id IS NOT NULL AND cycle_count_id IS NULL AND entity_type = 'InventoryMovement' AND target_id = movement_id)
        OR (movement_id IS NULL AND cycle_count_id IS NOT NULL AND entity_type = 'CycleCount' AND target_id = cycle_count_id))
);

CREATE TABLE IF NOT EXISTS EventHistory (
    event_history_id VARCHAR(13) PRIMARY KEY CHECK (event_history_id ~ '^HIST-[0-9]{8}$'),
    entity_type VARCHAR(24) NOT NULL CHECK (entity_type IN ('InventoryMovement','CycleCount','Investigation')),
    target_id VARCHAR(24) NOT NULL,
    movement_id VARCHAR(20) REFERENCES InventoryMovement(movement_id),
    cycle_count_id VARCHAR(19) REFERENCES CycleCount(cycle_count_id),
    investigation_id VARCHAR(18) REFERENCES Investigation(investigation_id),
    version INTEGER NOT NULL CHECK (version > 0),
    event_type VARCHAR(32) NOT NULL CHECK (event_type IN ('ANOMALY_INJECTION','STATUS_INITIALIZED','STATUS_TRANSITION')),
    event_timestamp TIMESTAMP NOT NULL,
    before_json TEXT,
    after_json TEXT NOT NULL,
    reason_code VARCHAR(40) NOT NULL,
    UNIQUE (entity_type, target_id, version),
    CHECK ((movement_id IS NOT NULL AND cycle_count_id IS NULL AND investigation_id IS NULL AND entity_type = 'InventoryMovement' AND target_id = movement_id)
        OR (movement_id IS NULL AND cycle_count_id IS NOT NULL AND investigation_id IS NULL AND entity_type = 'CycleCount' AND target_id = cycle_count_id)
        OR (movement_id IS NULL AND cycle_count_id IS NULL AND investigation_id IS NOT NULL AND entity_type = 'Investigation' AND target_id = investigation_id))
);

CREATE TABLE IF NOT EXISTS EvaluationMetrics (
    issue_type VARCHAR(40) PRIMARY KEY CHECK (issue_type IN ('Wrong_Store_Transfer','No_Reason_Adjustment','Time_Travel_Count','Double_Shrinkage_Transfer','Overall_Micro')),
    candidate_population BIGINT NOT NULL CHECK (candidate_population >= 0),
    true_positive BIGINT NOT NULL CHECK (true_positive >= 0),
    false_positive BIGINT NOT NULL CHECK (false_positive >= 0),
    true_negative BIGINT NOT NULL CHECK (true_negative >= 0),
    false_negative BIGINT NOT NULL CHECK (false_negative >= 0),
    precision NUMERIC(9,8) NOT NULL CHECK (precision BETWEEN 0 AND 1),
    recall NUMERIC(9,8) NOT NULL CHECK (recall BETWEEN 0 AND 1),
    f1_score NUMERIC(9,8) NOT NULL CHECK (f1_score BETWEEN 0 AND 1)
);

CREATE INDEX IF NOT EXISTS idx_movement_store_sku_time ON InventoryMovement(store_id, sku_id, "timestamp");
CREATE INDEX IF NOT EXISTS idx_count_store_sku_time ON CycleCount(store_id, sku_id, "timestamp");
CREATE INDEX IF NOT EXISTS idx_alert_store_time ON ShrinkageAlert(store_id, "timestamp");
