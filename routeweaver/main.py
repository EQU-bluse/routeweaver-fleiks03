from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .db import connect, initialize, transaction
from .models import (
    DispatchBatchResult,
    DispatchBatchSummary,
    DispatchPlan,
    Order,
    OrderCreate,
    Vehicle,
    VehicleCreate,
)
from .services import (
    NothingToCommitError,
    build_dispatch_plan,
    commit_dispatch,
    get_batch,
    list_batches,
)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize()
    yield


app = FastAPI(title="RouteWeaver", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "routeweaver"}


@app.get("/api/orders", response_model=list[Order])
def list_orders() -> list[dict]:
    with connect() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM orders ORDER BY due_at, id")]


@app.post("/api/orders", response_model=Order, status_code=201)
def create_order(payload: OrderCreate) -> dict:
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        with transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO orders(
                       reference, origin, destination, weight_kg, due_at,
                       required_license, status, created_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (
                    payload.reference,
                    payload.origin,
                    payload.destination,
                    payload.weight_kg,
                    payload.due_at.isoformat(),
                    payload.required_license,
                    created_at,
                ),
            )
            row = connection.execute("SELECT * FROM orders WHERE id = ?", (cursor.lastrowid,)).fetchone()
    except Exception as error:
        if "UNIQUE constraint failed" in str(error):
            raise HTTPException(status_code=409, detail="order reference already exists") from error
        raise
    return dict(row)


def _vehicles_with_licenses(connection) -> list[dict]:
    """Vehicles in id order, each with its driver and de-duplicated licenses."""
    licenses: dict[int, list[str]] = {}
    for row in connection.execute(
        "SELECT vehicle_id, license_code FROM vehicle_licenses ORDER BY vehicle_id, position"
    ):
        licenses.setdefault(row["vehicle_id"], []).append(row["license_code"])

    vehicles: list[dict] = []
    for row in connection.execute("SELECT * FROM vehicles ORDER BY id"):
        vehicle = dict(row)
        vehicle["licenses"] = licenses.get(vehicle["id"], [])
        vehicles.append(vehicle)
    return vehicles


@app.get("/api/vehicles", response_model=list[Vehicle])
def list_vehicles() -> list[dict]:
    with connect() as connection:
        return _vehicles_with_licenses(connection)


@app.post("/api/vehicles", response_model=Vehicle, status_code=201)
def create_vehicle(payload: VehicleCreate) -> dict:
    try:
        with transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO vehicles(code, capacity_kg, driver_name, status)
                   VALUES (?, ?, ?, 'available')""",
                (payload.code, payload.capacity_kg, payload.driver_name),
            )
            vehicle_id = cursor.lastrowid
            connection.executemany(
                "INSERT INTO vehicle_licenses(vehicle_id, license_code, position) VALUES (?, ?, ?)",
                [(vehicle_id, license_code, position)
                 for position, license_code in enumerate(payload.licenses)],
            )
    except Exception as error:
        if "UNIQUE constraint failed" in str(error):
            raise HTTPException(status_code=409, detail="vehicle code already exists") from error
        raise

    with connect() as connection:
        return next(
            vehicle
            for vehicle in _vehicles_with_licenses(connection)
            if vehicle["id"] == vehicle_id
        )


@app.post("/api/dispatch/plan", response_model=DispatchPlan)
def plan_dispatch() -> DispatchPlan:
    with connect() as connection:
        return build_dispatch_plan(connection)


@app.post("/api/dispatch/commit", response_model=DispatchBatchResult)
def commit_dispatch_plan() -> dict:
    try:
        return commit_dispatch()
    except NothingToCommitError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/api/dispatch/batches", response_model=list[DispatchBatchSummary])
def list_dispatch_batches() -> list[dict]:
    with connect() as connection:
        return list_batches(connection)


@app.get("/api/dispatch/batches/{batch_id}", response_model=DispatchBatchResult)
def get_dispatch_batch(batch_id: int) -> dict:
    with connect() as connection:
        batch = get_batch(connection, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="dispatch batch not found")
    return batch

