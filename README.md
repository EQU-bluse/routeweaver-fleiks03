# RouteWeaver

RouteWeaver is a local-first logistics order and fleet dispatch service built with FastAPI and SQLite. The repository ships only synthetic demo data.

## Run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
uvicorn routeweaver.main:app --reload
```

Open `http://127.0.0.1:8000/`. A terminal demo is available with `python -m routeweaver.cli demo`.

## Test

```bash
pytest -q
```

## Public interfaces

- `GET /health`
- `GET /api/orders` and `POST /api/orders`
- `GET /api/vehicles`
- `POST /api/dispatch/plan`
- `POST /api/dispatch/commit`
- `GET /api/dispatch/batches` and `GET /api/dispatch/batches/{batch_id}`

The baseline planner is deterministic and local. It assigns only pending orders whose weight fits an available vehicle; it does not call external maps or use live customer data. `POST /api/dispatch/plan` is a read-only preview. `POST /api/dispatch/commit` recomputes the same plan inside one transaction, persists it as a batch (orders become `planned`, vehicles become `assigned`), and returns `409` without writing anything when there is nothing confirmable.

