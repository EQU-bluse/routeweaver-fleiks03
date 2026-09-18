from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import initialize, transaction
from .services import build_dispatch_plan


def demo() -> None:
    with tempfile.TemporaryDirectory(prefix="routeweaver-") as directory:
        path = Path(directory) / "demo.db"
        initialize(path)
        now = datetime.now(timezone.utc)
        with transaction(path) as connection:
            connection.executemany(
                """INSERT INTO orders(reference, origin, destination, weight_kg, due_at, status, created_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                [
                    ("RW-DEMO-001", "North Hub", "Harbor Store", 850, (now + timedelta(hours=2)).isoformat(), now.isoformat()),
                    ("RW-DEMO-002", "North Hub", "Airport Depot", 3400, (now + timedelta(hours=5)).isoformat(), now.isoformat()),
                ],
            )
            plan = build_dispatch_plan(connection)
        print(json.dumps(plan.model_dump(mode="json"), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="routeweaver")
    parser.add_argument("command", choices=["demo"])
    args = parser.parse_args()
    if args.command == "demo":
        demo()


if __name__ == "__main__":
    main()

