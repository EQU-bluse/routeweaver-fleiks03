from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading

from fastapi.testclient import TestClient

from routeweaver.db import connect
from routeweaver.main import app


def _db_file(tmp_path) -> str:
    return str(tmp_path / "routeweaver.db")


def _order_payload(reference: str, weight_kg: float, due_hours: float) -> dict:
    return {
        "reference": reference,
        "origin": "Alpha Hub",
        "destination": "Beta Store",
        "weight_kg": weight_kg,
        "due_at": (datetime.now(timezone.utc) + timedelta(hours=due_hours)).isoformat(),
    }


def _create_order(client, reference: str, weight_kg: float, due_hours: float = 4) -> dict:
    response = client.post("/api/orders", json=_order_payload(reference, weight_kg, due_hours))
    assert response.status_code == 201
    return response.json()


def test_health_and_seeded_vehicles(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", str(tmp_path / "routeweaver.db"))
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok", "service": "routeweaver"}
        vehicles = client.get("/api/vehicles").json()
        assert [vehicle["code"] for vehicle in vehicles] == ["VAN-01", "TRUCK-07", "TRUCK-12"]


def test_create_order_and_build_explainable_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", str(tmp_path / "routeweaver.db"))
    due_at = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
    with TestClient(app) as client:
        response = client.post(
            "/api/orders",
            json={
                "reference": "RW-TEST-001",
                "origin": "Alpha Hub",
                "destination": "Beta Store",
                "weight_kg": 900,
                "due_at": due_at,
            },
        )
        assert response.status_code == 201
        plan = client.post("/api/dispatch/plan").json()
        assert len(plan["assignments"]) == 1
        assert plan["assignments"][0]["vehicle_code"] == "VAN-01"
        assert "fits" in plan["assignments"][0]["reason"]


def test_duplicate_reference_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", str(tmp_path / "routeweaver.db"))
    payload = {
        "reference": "RW-DUP-001",
        "origin": "A",
        "destination": "B",
        "weight_kg": 10,
        "due_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    }
    with TestClient(app) as client:
        assert client.post("/api/orders", json=payload).status_code == 201
        assert client.post("/api/orders", json=payload).status_code == 409


def test_plan_remains_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", _db_file(tmp_path))
    with TestClient(app) as client:
        _create_order(client, "RW-PREVIEW-001", 900)
        before_orders = client.get("/api/orders").json()
        before_vehicles = client.get("/api/vehicles").json()

        response = client.post("/api/dispatch/plan")
        assert response.status_code == 200
        plan = response.json()
        assert set(plan) == {"generated_at", "assignments", "unassigned_order_ids", "unassigned_reasons"}
        assert len(plan["assignments"]) == 1

        # Nothing changed: no batches, order still pending, vehicle still available.
        assert client.get("/api/dispatch/batches").json() == []
        assert client.get("/api/orders").json() == before_orders
        assert client.get("/api/vehicles").json() == before_vehicles


def test_commit_confirms_plan_and_flips_status(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", _db_file(tmp_path))
    with TestClient(app) as client:
        first = _create_order(client, "RW-COMMIT-001", 850, due_hours=1)
        second = _create_order(client, "RW-COMMIT-002", 7_000, due_hours=2)
        third = _create_order(client, "RW-COMMIT-003", 300, due_hours=3)
        heavy = _create_order(client, "RW-COMMIT-004", 2_000, due_hours=4)

        preview = client.post("/api/dispatch/plan").json()

        response = client.post("/api/dispatch/commit")
        assert response.status_code == 200
        result = response.json()
        assert set(result) == {
            "batch_id",
            "created_at",
            "assignments",
            "unassigned_order_ids",
            "unassigned_reasons",
        }
        assert result["batch_id"] == 1
        assert result["created_at"]

        # Commit recomputes the exact same deterministic rule as the preview.
        preview_assignments = [
            {key: item[key] for key in ("order_id", "vehicle_id", "vehicle_code", "reason")}
            for item in preview["assignments"]
        ]
        assert result["assignments"] == preview_assignments
        assert [item["order_id"] for item in result["assignments"]] == [
            first["id"],
            second["id"],
            third["id"],
        ]
        assert [item["vehicle_code"] for item in result["assignments"]] == [
            "VAN-01",
            "TRUCK-07",
            "VAN-01",
        ]
        assert all("fits" in item["reason"] for item in result["assignments"])
        assert result["unassigned_order_ids"] == [heavy["id"]]

        # Orders and vehicles transition exactly as specified.
        statuses = {order["id"]: order["status"] for order in client.get("/api/orders").json()}
        assert statuses[first["id"]] == "planned"
        assert statuses[second["id"]] == "planned"
        assert statuses[third["id"]] == "planned"
        assert statuses[heavy["id"]] == "pending"
        vehicles = {vehicle["code"]: vehicle["status"] for vehicle in client.get("/api/vehicles").json()}
        assert vehicles["VAN-01"] == "assigned"
        assert vehicles["TRUCK-07"] == "assigned"
        assert vehicles["TRUCK-12"] == "maintenance"

        # Summary and detail endpoints agree.
        summaries = client.get("/api/dispatch/batches").json()
        assert summaries == [
            {
                "batch_id": 1,
                "created_at": result["created_at"],
                "assignment_count": 3,
                "unassigned_count": 1,
            }
        ]
        assert client.get("/api/dispatch/batches/1").json() == result


def test_commit_then_new_orders_creates_second_batch_in_order(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", _db_file(tmp_path))
    with TestClient(app) as client:
        first_order = _create_order(client, "RW-BATCH-A", 900)
        first = client.post("/api/dispatch/commit").json()
        assert first["batch_id"] == 1

        # After the first batch only TRUCK-07 is available, so the next order lands there.
        second_order = _create_order(client, "RW-BATCH-B", 2_000)
        second = client.post("/api/dispatch/commit").json()
        assert second["batch_id"] == 2
        assert second["assignments"][0]["order_id"] == second_order["id"]
        assert second["assignments"][0]["vehicle_code"] == "TRUCK-07"
        assert all(item["order_id"] != first_order["id"] for item in second["assignments"])

        summaries = client.get("/api/dispatch/batches").json()
        assert [batch["batch_id"] for batch in summaries] == [1, 2]


def test_commit_409_without_pending_orders_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", _db_file(tmp_path))
    with TestClient(app) as client:
        _create_order(client, "RW-EMPTY-001", 900)
        assert client.post("/api/dispatch/commit").status_code == 200

        response = client.post("/api/dispatch/commit")
        assert response.status_code == 409

        # Exactly one batch still exists; the failed commit added no empty batch.
        summaries = client.get("/api/dispatch/batches").json()
        assert len(summaries) == 1
        assert summaries[0]["assignment_count"] == 1
        assert summaries[0]["unassigned_count"] == 0


def test_commit_409_without_available_vehicles_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", _db_file(tmp_path))
    with TestClient(app) as client:
        _create_order(client, "RW-USED-001", 5_000, due_hours=1)
        _create_order(client, "RW-USED-002", 900, due_hours=2)
        assert client.post("/api/dispatch/commit").status_code == 200

        pending = _create_order(client, "RW-USED-003", 100, due_hours=3)
        response = client.post("/api/dispatch/commit")
        assert response.status_code == 409

        # No new batch, the new order stays pending, and vehicles stay assigned.
        assert [batch["batch_id"] for batch in client.get("/api/dispatch/batches").json()] == [1]
        order = next(o for o in client.get("/api/orders").json() if o["id"] == pending["id"])
        assert order["status"] == "pending"
        assert all(
            vehicle["status"] == "assigned"
            for vehicle in client.get("/api/vehicles").json()
            if vehicle["code"] in ("VAN-01", "TRUCK-07")
        )


def test_commit_409_when_nothing_fits_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", _db_file(tmp_path))
    with TestClient(app) as client:
        _create_order(client, "RW-HEAVY-001", 50_000)
        response = client.post("/api/dispatch/commit")
        assert response.status_code == 409

        assert client.get("/api/dispatch/batches").json() == []
        assert client.get("/api/orders").json()[0]["status"] == "pending"
        assert all(
            vehicle["status"] == "available"
            for vehicle in client.get("/api/vehicles").json()
            if vehicle["code"] in ("VAN-01", "TRUCK-07")
        )


def test_unknown_batch_returns_404(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", _db_file(tmp_path))
    with TestClient(app) as client:
        assert client.get("/api/dispatch/batches/999").status_code == 404


def test_batch_survives_service_restart(tmp_path, monkeypatch):
    db_file = _db_file(tmp_path)
    monkeypatch.setenv("ROUTEWEAVER_DB", db_file)
    with TestClient(app) as client:
        _create_order(client, "RW-RESTART-001", 900, due_hours=1)
        _create_order(client, "RW-RESTART-002", 60_000, due_hours=2)
        committed = client.post("/api/dispatch/commit").json()

    # Simulate a restart against the same on-disk SQLite database.
    with TestClient(app) as client:
        summaries = client.get("/api/dispatch/batches").json()
        assert [batch["batch_id"] for batch in summaries] == [1]
        assert client.get("/api/dispatch/batches/1").json() == committed

        with connect(Path(db_file)) as connection:
            assert connection.execute("SELECT COUNT(*) FROM dispatch_batches").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM dispatch_assignments").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM dispatch_unassigned").fetchone()[0] == 1


def test_concurrent_commits_never_double_confirm(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", _db_file(tmp_path))
    with TestClient(app) as client:
        order_a = _create_order(client, "RW-RACE-001", 900, due_hours=1)
        order_b = _create_order(client, "RW-RACE-002", 2_000, due_hours=2)

        results: list[tuple[int, object]] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(4)

        def worker() -> None:
            try:
                barrier.wait()
                response = client.post("/api/dispatch/commit")
                body = response.json() if response.content else None
                results.append((response.status_code, body))
            except Exception as error:  # pragma: no cover - surfaced via assertions below
                errors.append(error)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert sorted(status for status, _ in results) == [200, 409, 409, 409]
        success = next(body for status, body in results if status == 200)
        assert sorted(item["order_id"] for item in success["assignments"]) == [
            order_a["id"],
            order_b["id"],
        ]

        # Exactly one batch; no order or vehicle is confirmed twice.
        assert len(client.get("/api/dispatch/batches").json()) == 1
        detail = client.get("/api/dispatch/batches/1").json()
        order_ids = [item["order_id"] for item in detail["assignments"]]
        vehicle_ids = [item["vehicle_id"] for item in detail["assignments"]]
        assert len(order_ids) == len(set(order_ids)) == 2
        assert len(vehicle_ids) == len(set(vehicle_ids))
        assert all(order["status"] == "planned" for order in client.get("/api/orders").json())
        vehicles = {vehicle["code"]: vehicle["status"] for vehicle in client.get("/api/vehicles").json()}
        assert vehicles["VAN-01"] == "assigned"
        assert vehicles["TRUCK-07"] == "assigned"



def test_order_with_required_license_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", _db_file(tmp_path))
    with TestClient(app) as client:
        payload = _order_payload("RW-LIC-001", 500, 4)
        payload["required_license"] = "H2"
        response = client.post("/api/orders", json=payload)
        assert response.status_code == 201
        assert response.json()["required_license"] == "H2"

        # Omitting the field (or null) stays null and is returned as such.
        plain = _create_order(client, "RW-LIC-002", 100)
        assert plain["required_license"] is None
        payload_null = _order_payload("RW-LIC-003", 100, 4)
        payload_null["required_license"] = None
        assert client.post("/api/orders", json=payload_null).status_code == 201

        orders = {order["reference"]: order for order in client.get("/api/orders").json()}
        assert orders["RW-LIC-001"]["required_license"] == "H2"
        assert orders["RW-LIC-002"]["required_license"] is None


def test_order_required_license_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", _db_file(tmp_path))
    with TestClient(app) as client:
        too_long = _order_payload("RW-LIC-LONG", 100, 4)
        too_long["required_license"] = "X" * 33
        assert client.post("/api/orders", json=too_long).status_code == 422

        empty = _order_payload("RW-LIC-EMPTY", 100, 4)
        empty["required_license"] = ""
        assert client.post("/api/orders", json=empty).status_code == 422

        ok = _order_payload("RW-LIC-MAX", 100, 4)
        ok["required_license"] = "X" * 32
        assert client.post("/api/orders", json=ok).status_code == 201


def test_create_vehicle_and_list_with_driver_and_licenses(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", _db_file(tmp_path))
    with TestClient(app) as client:
        response = client.post(
            "/api/vehicles",
            json={
                "code": "VAN-99",
                "capacity_kg": 1500,
                "driver_name": "Rio Chen",
                "licenses": ["C1", "H2", "C1"],
            },
        )
        assert response.status_code == 201
        created = response.json()
        assert created["code"] == "VAN-99"
        assert created["driver_name"] == "Rio Chen"
        assert created["licenses"] == ["C1", "H2"]
        assert created["status"] == "available"

        vehicles = client.get("/api/vehicles").json()
        assert [vehicle["code"] for vehicle in vehicles] == ["VAN-01", "TRUCK-07", "TRUCK-12", "VAN-99"]
        by_code = {vehicle["code"]: vehicle for vehicle in vehicles}
        assert by_code["VAN-99"]["driver_name"] == "Rio Chen"
        assert by_code["VAN-99"]["licenses"] == ["C1", "H2"]

        # Seeded demo vehicles carry driver and capability data too.
        assert by_code["VAN-01"]["driver_name"]
        assert by_code["TRUCK-07"]["licenses"] == ["C1", "H2"]

        # Duplicate code conflicts; missing driver is rejected.
        assert client.post(
            "/api/vehicles",
            json={"code": "VAN-99", "capacity_kg": 10, "driver_name": "Other", "licenses": []},
        ).status_code == 409
        assert client.post(
            "/api/vehicles",
            json={"code": "VAN-100", "capacity_kg": 10, "driver_name": "", "licenses": []},
        ).status_code == 422


def test_plan_respects_required_license(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", _db_file(tmp_path))
    with TestClient(app) as client:
        # Only TRUCK-07 holds H2 among available vehicles (VAN-01 has C1).
        order = _create_order(client, "RW-HAZ-001", 500, due_hours=1)
        payload = _order_payload("RW-HAZ-002", 500, 2)
        payload["required_license"] = "H2"
        licensed = client.post("/api/orders", json=payload).json()

        plan = client.post("/api/dispatch/plan").json()
        by_order = {item["order_id"]: item for item in plan["assignments"]}
        assert by_order[order["id"]]["vehicle_code"] == "VAN-01"
        assert by_order[licensed["id"]]["vehicle_code"] == "TRUCK-07"
        assert plan["unassigned_order_ids"] == []
        assert plan["unassigned_reasons"] == {}


def test_unassigned_reasons_for_license_and_capacity(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", _db_file(tmp_path))
    with TestClient(app) as client:
        # No available vehicle carries license "CRANE".
        payload = _order_payload("RW-NOLIC-001", 100, 1)
        payload["required_license"] = "CRANE"
        no_license = client.post("/api/orders", json=payload).json()
        # Far heavier than any available vehicle.
        heavy = _create_order(client, "RW-TOOHEAVY-001", 90_000, due_hours=2)

        plan = client.post("/api/dispatch/plan").json()
        assert plan["assignments"] == []
        assert plan["unassigned_order_ids"] == [no_license["id"], heavy["id"]]
        reasons = plan["unassigned_reasons"]
        assert "license" in reasons[str(no_license["id"])].lower()
        assert "CRANE" in reasons[str(no_license["id"])]
        assert "capacity" in reasons[str(heavy["id"])].lower()

        # Zero feasible assignments: commit is a 409 and persists nothing.
        assert client.post("/api/dispatch/commit").status_code == 409
        assert client.get("/api/dispatch/batches").json() == []
        assert all(
            order["status"] == "pending" for order in client.get("/api/orders").json()
        )


def test_commit_persists_unassigned_reasons_across_restart(tmp_path, monkeypatch):
    db_file = _db_file(tmp_path)
    monkeypatch.setenv("ROUTEWEAVER_DB", db_file)
    with TestClient(app) as client:
        ok = _create_order(client, "RW-MIX-001", 900, due_hours=1)
        payload = _order_payload("RW-MIX-002", 100, 2)
        payload["required_license"] = "CRANE"
        blocked = client.post("/api/orders", json=payload).json()

        response = client.post("/api/dispatch/commit")
        assert response.status_code == 200
        result = response.json()
        assert [item["order_id"] for item in result["assignments"]] == [ok["id"]]
        assert result["unassigned_order_ids"] == [blocked["id"]]
        assert "license" in result["unassigned_reasons"][str(blocked["id"])].lower()

        assert client.get("/api/dispatch/batches/1").json() == result

    # After a restart the batch detail returns the same reasons verbatim.
    with TestClient(app) as client:
        detail = client.get("/api/dispatch/batches/1").json()
        assert detail["unassigned_order_ids"] == [blocked["id"]]
        assert detail["unassigned_reasons"] == result["unassigned_reasons"]


def test_license_mismatch_only_yields_409_without_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", _db_file(tmp_path))
    with TestClient(app) as client:
        payload = _order_payload("RW-ONLY-LIC-001", 100, 1)
        payload["required_license"] = "CRANE"
        client.post("/api/orders", json=payload)

        response = client.post("/api/dispatch/commit")
        assert response.status_code == 409
        assert client.get("/api/dispatch/batches").json() == []
        assert client.get("/api/orders").json()[0]["status"] == "pending"
        assert all(
            vehicle["status"] == "available"
            for vehicle in client.get("/api/vehicles").json()
            if vehicle["code"] in ("VAN-01", "TRUCK-07")
        )
