from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .models import DispatchAssignment, DispatchPlan


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

