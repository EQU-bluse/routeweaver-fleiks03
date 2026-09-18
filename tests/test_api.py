from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from routeweaver.main import app


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

