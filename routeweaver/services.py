from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from .db import transaction
from .models import DispatchAssignment, DispatchPlan


class NothingToCommitError(Exception):
    """Raised when a commit would produce no confirmed assignments."""


def _vehicle_licenses(connection: sqlite3.Connection) -> dict[int, list[str]]:
    """Capability credentials per vehicle, in their stored order."""
    licenses: dict[int, list[str]] = {}
    for row in connection.execute(
        "SELECT vehicle_id, license_code FROM vehicle_licenses ORDER BY vehicle_id, position"
    ):
        licenses.setdefault(row["vehicle_id"], []).append(row["license_code"])
    return licenses


def _capacity_reason(weight_kg: float) -> str:
    return f"no available vehicle can carry {weight_kg:g} kg"


def _license_reason(required_license: str) -> str:
    return f"no available vehicle holds the required license '{required_license}'"


def _compute_plan(connection: sqlite3.Connection, generated_at: datetime) -> DispatchPlan:
    """Apply the deterministic planning rule against the current database state.

    Orders are processed by (due_at, id); each order goes onto the available
    vehicle (ordered by capacity, id) whose remaining capacity leaves the
    tightest fit. An order that declares a ``required_license`` can only use a
    vehicle whose driver holds that credential. This function is read-only and
    is shared by the preview and commit paths so both always agree.
    """
    orders = connection.execute(
        "SELECT * FROM orders WHERE status = 'pending' ORDER BY due_at, id"
    ).fetchall()
    vehicles = connection.execute(
        "SELECT * FROM vehicles WHERE status = 'available' ORDER BY capacity_kg, id"
    ).fetchall()
    licenses = _vehicle_licenses(connection)

    remaining = {vehicle["id"]: float(vehicle["capacity_kg"]) for vehicle in vehicles}
    assignments: list[DispatchAssignment] = []
    unassigned: list[int] = []
    unassigned_reasons: dict[int, str] = {}

    for order in orders:
        weight_kg = float(order["weight_kg"])
        required_license = order["required_license"]

        capacity_matches = [
            vehicle for vehicle in vehicles if remaining[vehicle["id"]] >= weight_kg
        ]
        if not capacity_matches:
            order_id = order["id"]
            unassigned.append(order_id)
            unassigned_reasons[order_id] = _capacity_reason(weight_kg)
            continue

        candidates = [
            vehicle
            for vehicle in capacity_matches
            if required_license is None
            or required_license in licenses.get(vehicle["id"], [])
        ]
        if not candidates:
            order_id = order["id"]
            unassigned.append(order_id)
            unassigned_reasons[order_id] = _license_reason(required_license)
            continue

        vehicle = min(
            candidates,
            key=lambda item: (remaining[item["id"]] - weight_kg, item["id"]),
        )
        remaining[vehicle["id"]] -= weight_kg
        assignments.append(
            DispatchAssignment(
                order_id=order["id"],
                vehicle_id=vehicle["id"],
                vehicle_code=vehicle["code"],
                reason=(
                    f"earliest due order; {weight_kg:g} kg fits "
                    f"{vehicle['capacity_kg']:g} kg capacity"
                ),
            )
        )

    return DispatchPlan(
        generated_at=generated_at,
        assignments=assignments,
        unassigned_order_ids=unassigned,
        unassigned_reasons=unassigned_reasons,
    )


def build_dispatch_plan(connection: sqlite3.Connection) -> DispatchPlan:
    """Read-only deterministic preview; never mutates orders, vehicles or history."""
    return _compute_plan(connection, datetime.now(timezone.utc))


def commit_dispatch() -> dict[str, Any]:
    """Recompute the plan and persist it as a confirmed batch atomically.

    Returns the batch result. Raises NothingToCommitError when there is nothing
    confirmable (no pending orders, no available vehicles, or no feasible
    assignment); in that case the transaction is rolled back without creating a
    batch or touching any status.
    """
    created_at = datetime.now(timezone.utc)
    with transaction() as connection:
        plan = _compute_plan(connection, created_at)

        if not plan.assignments:
            raise NothingToCommitError(
                "no confirmable dispatch: pending orders or available vehicles missing"
            )

        cursor = connection.execute(
            "INSERT INTO dispatch_batches(created_at) VALUES (?)",
            (created_at.isoformat(),),
        )
        batch_id = cursor.lastrowid

        connection.executemany(
            """INSERT INTO dispatch_assignments(
                   batch_id, order_id, vehicle_id, vehicle_code, reason, position
               ) VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (
                    batch_id,
                    assignment.order_id,
                    assignment.vehicle_id,
                    assignment.vehicle_code,
                    assignment.reason,
                    position,
                )
                for position, assignment in enumerate(plan.assignments)
            ],
        )
        connection.executemany(
            "INSERT INTO dispatch_unassigned(batch_id, order_id, reason, position) VALUES (?, ?, ?, ?)",
            [
                (batch_id, order_id, plan.unassigned_reasons[order_id], position)
                for position, order_id in enumerate(plan.unassigned_order_ids)
            ],
        )

        assigned_order_ids = [assignment.order_id for assignment in plan.assignments]
        assigned_vehicle_ids = list(
            {assignment.vehicle_id for assignment in plan.assignments}
        )

        order_result = connection.execute(
            f"UPDATE orders SET status = 'planned' "
            f"WHERE id IN ({','.join('?' for _ in assigned_order_ids)}) AND status = 'pending'",
            assigned_order_ids,
        )
        vehicle_result = connection.execute(
            f"UPDATE vehicles SET status = 'assigned' "
            f"WHERE id IN ({','.join('?' for _ in assigned_vehicle_ids)}) AND status = 'available'",
            assigned_vehicle_ids,
        )

        # Defensive invariant: the plan was computed inside this same locked
        # transaction, so every target row must still be in the expected state.
        # A mismatch means a concurrent writer changed state mid-commit; refuse
        # rather than double-confirming an order or vehicle.
        if order_result.rowcount != len(assigned_order_ids):
            raise NothingToCommitError("orders changed concurrently; no batch created")
        if vehicle_result.rowcount != len(assigned_vehicle_ids):
            raise NothingToCommitError("vehicles changed concurrently; no batch created")

    return {
        "batch_id": batch_id,
        "created_at": created_at,
        "assignments": [assignment.model_dump() for assignment in plan.assignments],
        "unassigned_order_ids": plan.unassigned_order_ids,
        "unassigned_reasons": plan.unassigned_reasons,
    }


def list_batches(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Batch summaries ordered by creation time then batch id."""
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT b.id AS batch_id,
                   b.created_at,
                   COUNT(DISTINCT a.id) AS assignment_count,
                   COUNT(DISTINCT u.order_id) AS unassigned_count
            FROM dispatch_batches AS b
            LEFT JOIN dispatch_assignments AS a ON a.batch_id = b.id
            LEFT JOIN dispatch_unassigned AS u ON u.batch_id = b.id
            GROUP BY b.id
            ORDER BY b.created_at, b.id
            """
        )
    ]


def get_batch(connection: sqlite3.Connection, batch_id: int) -> dict[str, Any] | None:
    """Full confirmed result for one batch, or None if it does not exist."""
    batch = connection.execute(
        "SELECT id, created_at FROM dispatch_batches WHERE id = ?", (batch_id,)
    ).fetchone()
    if batch is None:
        return None

    assignments = [
        {
            "order_id": row["order_id"],
            "vehicle_id": row["vehicle_id"],
            "vehicle_code": row["vehicle_code"],
            "reason": row["reason"],
        }
        for row in connection.execute(
            """SELECT order_id, vehicle_id, vehicle_code, reason
               FROM dispatch_assignments
               WHERE batch_id = ?
               ORDER BY position, id""",
            (batch_id,),
        )
    ]
    unassigned_rows = connection.execute(
        "SELECT order_id, reason FROM dispatch_unassigned WHERE batch_id = ? ORDER BY position",
        (batch_id,),
    ).fetchall()
    unassigned_order_ids = [row["order_id"] for row in unassigned_rows]
    unassigned_reasons = {row["order_id"]: row["reason"] for row in unassigned_rows}

    return {
        "batch_id": batch["id"],
        "created_at": batch["created_at"],
        "assignments": assignments,
        "unassigned_order_ids": unassigned_order_ids,
        "unassigned_reasons": unassigned_reasons,
    }
