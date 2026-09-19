from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .models import DispatchAssignment, DispatchBatch, DispatchBatchSummary, DispatchPlan


def build_dispatch_plan(connection: sqlite3.Connection) -> DispatchPlan:
    orders = connection.execute(
        "SELECT * FROM orders WHERE status = 'pending' ORDER BY due_at, id"
    ).fetchall()
    vehicles = connection.execute(
        "SELECT * FROM vehicles WHERE status = 'available' ORDER BY capacity_kg, id"
    ).fetchall()

    remaining = {vehicle["id"]: float(vehicle["capacity_kg"]) for vehicle in vehicles}
    assignments: list[DispatchAssignment] = []
    unassigned: list[int] = []

    for order in orders:
        candidates = [
            vehicle
            for vehicle in vehicles
            if remaining[vehicle["id"]] >= float(order["weight_kg"])
        ]
        if not candidates:
            unassigned.append(order["id"])
            continue
        vehicle = min(
            candidates,
            key=lambda item: (remaining[item["id"]] - float(order["weight_kg"]), item["id"]),
        )
        remaining[vehicle["id"]] -= float(order["weight_kg"])
        assignments.append(
            DispatchAssignment(
                order_id=order["id"],
                vehicle_id=vehicle["id"],
                vehicle_code=vehicle["code"],
                reason=(
                    f"earliest due order; {order['weight_kg']:g} kg fits "
                    f"{vehicle['capacity_kg']:g} kg capacity"
                ),
            )
        )

    return DispatchPlan(
        generated_at=datetime.now(timezone.utc),
        assignments=assignments,
        unassigned_order_ids=unassigned,
    )


def commit_dispatch_plan(connection: sqlite3.Connection) -> DispatchBatch | None:
    """Recompute the plan and persist it as a confirmed batch.

    Must be called inside a write transaction. Returns None when there is
    nothing confirmable (no pending orders, no available vehicles, or no
    order fits any vehicle); in that case nothing is written.
    """
    plan = build_dispatch_plan(connection)
    if not plan.assignments:
        return None

    created_at = datetime.now(timezone.utc)
    cursor = connection.execute(
        "INSERT INTO dispatch_batches(created_at, unassigned_order_ids) VALUES (?, ?)",
        (created_at.isoformat(), json.dumps(plan.unassigned_order_ids)),
    )
    batch_id = cursor.lastrowid
    for assignment in plan.assignments:
        connection.execute(
            """INSERT INTO dispatch_assignments(batch_id, order_id, vehicle_id, vehicle_code, reason)
               VALUES (?, ?, ?, ?, ?)""",
            (
                batch_id,
                assignment.order_id,
                assignment.vehicle_id,
                assignment.vehicle_code,
                assignment.reason,
            ),
        )
        connection.execute(
            "UPDATE orders SET status = 'planned' WHERE id = ? AND status = 'pending'",
            (assignment.order_id,),
        )
        connection.execute(
            "UPDATE vehicles SET status = 'assigned' WHERE id = ? AND status = 'available'",
            (assignment.vehicle_id,),
        )

    return DispatchBatch(
        batch_id=batch_id,
        created_at=created_at,
        assignments=plan.assignments,
        unassigned_order_ids=plan.unassigned_order_ids,
    )


def list_dispatch_batches(connection: sqlite3.Connection) -> list[DispatchBatchSummary]:
    rows = connection.execute(
        """SELECT b.id, b.created_at, b.unassigned_order_ids, COUNT(a.id) AS assignment_count
           FROM dispatch_batches b
           LEFT JOIN dispatch_assignments a ON a.batch_id = b.id
           GROUP BY b.id
           ORDER BY b.created_at, b.id"""
    ).fetchall()
    return [
        DispatchBatchSummary(
            batch_id=row["id"],
            created_at=row["created_at"],
            assignment_count=row["assignment_count"],
            unassigned_order_ids=json.loads(row["unassigned_order_ids"]),
        )
        for row in rows
    ]


def get_dispatch_batch(connection: sqlite3.Connection, batch_id: int) -> DispatchBatch | None:
    row = connection.execute(
        "SELECT * FROM dispatch_batches WHERE id = ?", (batch_id,)
    ).fetchone()
    if row is None:
        return None
    assignments = connection.execute(
        """SELECT order_id, vehicle_id, vehicle_code, reason
           FROM dispatch_assignments WHERE batch_id = ? ORDER BY id""",
        (batch_id,),
    ).fetchall()
    return DispatchBatch(
        batch_id=row["id"],
        created_at=row["created_at"],
        assignments=[DispatchAssignment(**dict(assignment)) for assignment in assignments],
        unassigned_order_ids=json.loads(row["unassigned_order_ids"]),
    )

