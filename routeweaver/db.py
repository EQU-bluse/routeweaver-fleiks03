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
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    capacity_kg REAL NOT NULL CHECK (capacity_kg > 0),
    status TEXT NOT NULL DEFAULT 'available'
);
CREATE TABLE IF NOT EXISTS dispatch_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    unassigned_order_ids TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS dispatch_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES dispatch_batches(id),
    order_id INTEGER NOT NULL,
    vehicle_id INTEGER NOT NULL,
    vehicle_code TEXT NOT NULL,
    reason TEXT NOT NULL
);
"""

SEED_VEHICLES = (
    ("VAN-01", 1_200.0, "available"),
    ("TRUCK-07", 8_000.0, "available"),
    ("TRUCK-12", 18_000.0, "maintenance"),
)


def database_path() -> Path:
    return Path(os.environ.get("ROUTEWEAVER_DB", "routeweaver.db")).resolve()


def connect(path: Path | None = None) -> sqlite3.Connection:
    connection = sqlite3.connect(path or database_path())
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize(path: Path | None = None) -> None:
    with connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.executemany(
            "INSERT OR IGNORE INTO vehicles(code, capacity_kg, status) VALUES (?, ?, ?)",
            SEED_VEHICLES,
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

