from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from routeweaver.main import app


def make_order_payload(reference: str, weight_kg: float, hours: int = 4) -> dict:
    return {
        "reference": reference,
        "origin": "Alpha Hub",
        "destination": "Beta Store",
        "weight_kg": weight_kg,
        "due_at": (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(),
    }


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


def test_commit_without_pending_orders_returns_409(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", str(tmp_path / "routeweaver.db"))
    with TestClient(app) as client:
        assert client.post("/api/dispatch/commit").status_code == 409
        assert client.get("/api/dispatch/batches").json() == []


def test_commit_persists_batch_and_updates_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", str(tmp_path / "routeweaver.db"))
    with TestClient(app) as client:
        assert client.post("/api/orders", json=make_order_payload("RW-C-001", 900, hours=2)).status_code == 201
        assert client.post("/api/orders", json=make_order_payload("RW-C-002", 5000, hours=6)).status_code == 201

        preview = client.post("/api/dispatch/plan").json()
        assert len(preview["assignments"]) == 2

        response = client.post("/api/dispatch/commit")
        assert response.status_code == 201
        batch = response.json()
        assert batch["batch_id"] >= 1
        assert batch["created_at"]
        assert [a["order_id"] for a in batch["assignments"]] == [
            a["order_id"] for a in preview["assignments"]
        ]
        assert batch["unassigned_order_ids"] == []
        assert all(a["vehicle_code"] and a["reason"] for a in batch["assignments"])

        orders = {order["reference"]: order for order in client.get("/api/orders").json()}
        assert orders["RW-C-001"]["status"] == "planned"
        assert orders["RW-C-002"]["status"] == "planned"
        vehicles = {vehicle["code"]: vehicle for vehicle in client.get("/api/vehicles").json()}
        assert vehicles["VAN-01"]["status"] == "assigned"
        assert vehicles["TRUCK-07"]["status"] == "assigned"
        assert vehicles["TRUCK-12"]["status"] == "maintenance"

        # Nothing left to confirm: no empty batch is written.
        assert client.post("/api/dispatch/commit").status_code == 409

        summaries = client.get("/api/dispatch/batches").json()
        assert len(summaries) == 1
        assert summaries[0]["batch_id"] == batch["batch_id"]
        assert summaries[0]["assignment_count"] == 2

        fetched = client.get(f"/api/dispatch/batches/{batch['batch_id']}").json()
        assert fetched == batch

        # Plan preview is now empty but still read-only and well-formed.
        plan = client.post("/api/dispatch/plan").json()
        assert plan["assignments"] == []
        assert plan["unassigned_order_ids"] == []


def test_commit_leaves_oversized_order_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", str(tmp_path / "routeweaver.db"))
    with TestClient(app) as client:
        assert client.post("/api/orders", json=make_order_payload("RW-C-010", 9000)).status_code == 201
        assert client.post("/api/orders", json=make_order_payload("RW-C-011", 100)).status_code == 201
        batch = client.post("/api/dispatch/commit").json()
        orders = {order["reference"]: order for order in client.get("/api/orders").json()}
        oversized_id = orders["RW-C-010"]["id"]
        assert batch["unassigned_order_ids"] == [oversized_id]
        assert orders["RW-C-010"]["status"] == "pending"
        assert orders["RW-C-011"]["status"] == "planned"
        fetched = client.get(f"/api/dispatch/batches/{batch['batch_id']}").json()
        assert fetched["unassigned_order_ids"] == [oversized_id]


def test_unknown_batch_returns_404(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", str(tmp_path / "routeweaver.db"))
    with TestClient(app) as client:
        assert client.get("/api/dispatch/batches/999").status_code == 404


def test_batches_survive_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", str(tmp_path / "routeweaver.db"))
    with TestClient(app) as client:
        assert client.post("/api/orders", json=make_order_payload("RW-R-001", 500)).status_code == 201
        batch = client.post("/api/dispatch/commit").json()
    with TestClient(app) as client:
        fetched = client.get(f"/api/dispatch/batches/{batch['batch_id']}")
        assert fetched.status_code == 200
        assert fetched.json() == batch
        assert [s["batch_id"] for s in client.get("/api/dispatch/batches").json()] == [batch["batch_id"]]


def test_concurrent_commits_do_not_double_assign(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEWEAVER_DB", str(tmp_path / "routeweaver.db"))
    with TestClient(app) as client:
        assert client.post("/api/orders", json=make_order_payload("RW-X-001", 900)).status_code == 201
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: client.post("/api/dispatch/commit").status_code, range(2)))
        assert sorted(statuses) == [201, 409]
        batches = client.get("/api/dispatch/batches").json()
        assert len(batches) == 1
        assert batches[0]["assignment_count"] == 1

