from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL UNIQUE,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    weight_kg REAL NOT NULL CHECK (weight_kg > 0),
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    required_license TEXT
);
CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    capacity_kg REAL NOT NULL CHECK (capacity_kg > 0),
    status TEXT NOT NULL DEFAULT 'available',
    driver_name TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS vehicle_licenses (
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    license_code TEXT NOT NULL,
    PRIMARY KEY (vehicle_id, license_code)
);
CREATE TABLE IF NOT EXISTS dispatch_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dispatch_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES dispatch_batches(id),
    order_id INTEGER NOT NULL REFERENCES orders(id),
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    vehicle_code TEXT NOT NULL,
    reason TEXT NOT NULL,
    position INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS dispatch_unassigned (
    batch_id INTEGER NOT NULL REFERENCES dispatch_batches(id),
    order_id INTEGER NOT NULL REFERENCES orders(id),
    position INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (batch_id, order_id)
);
"""

SEED_VEHICLES = (
    ("VAN-01", 1_200.0, "available", "Dana Kim", ("C1",)),
    ("TRUCK-07", 8_000.0, "available", "Lee Ortiz", ("C1", "H2")),
    ("TRUCK-12", 18_000.0, "maintenance", "Sam Patel", ("H2",)),
)


def database_path() -> Path:
    return Path(os.environ.get("ROUTEWEAVER_DB", "routeweaver.db")).resolve()


def connect(path: Path | None = None) -> sqlite3.Connection:
    connection = sqlite3.connect(path or database_path())
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Add a column to a pre-existing database that predates it."""
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def initialize(path: Path | None = None) -> None:
    with connect(path) as connection:
        connection.executescript(SCHEMA)
        _ensure_column(connection, "orders", "required_license", "required_license TEXT")
        _ensure_column(connection, "vehicles", "driver_name", "driver_name TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "dispatch_unassigned", "reason", "reason TEXT NOT NULL DEFAULT ''")
        for code, capacity_kg, status, driver_name, licenses in SEED_VEHICLES:
            connection.execute(
                "INSERT OR IGNORE INTO vehicles(code, capacity_kg, status, driver_name) "
                "VALUES (?, ?, ?, ?)",
                (code, capacity_kg, status, driver_name),
            )
            # Backfill the driver for demo vehicles created before drivers existed.
            connection.execute(
                "UPDATE vehicles SET driver_name = ? WHERE code = ? AND driver_name = ''",
                (driver_name, code),
            )
            vehicle_id = connection.execute(
                "SELECT id FROM vehicles WHERE code = ?", (code,)
            ).fetchone()[0]
            connection.executemany(
                "INSERT OR IGNORE INTO vehicle_licenses(vehicle_id, license_code) VALUES (?, ?)",
                [(vehicle_id, license) for license in licenses],
            )


@contextmanager
def transaction(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    connection = connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

