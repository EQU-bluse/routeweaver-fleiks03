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
- `GET /api/orders` and `POST /api/orders` — orders accept an optional `required_license` (1–32 character code, or `null`)
- `GET /api/vehicles` and `POST /api/vehicles` — vehicles carry a `driver_name` and a deduplicated `licenses` array
- `POST /api/dispatch/plan` — read-only deterministic preview for pending orders and available vehicles; never mutates state
- `POST /api/dispatch/commit` — recomputes the same plan inside one database transaction and confirms it as a persistent batch
- `GET /api/dispatch/batches` — batch summaries ordered by creation time and batch id
- `GET /api/dispatch/batches/{batch_id}` — full confirmed result for one batch (404 if unknown)

The baseline planner is deterministic and local. It assigns only pending orders whose weight fits an available vehicle; when an order declares a `required_license`, only vehicles whose `licenses` include it are candidates. It does not call external maps or use live customer data.

### Confirming dispatch

`POST /api/dispatch/commit` takes no request body. Within one SQLite transaction it
recomputes the plan with the exact same rule as the preview, then persists a batch
(`batch_id`, `created_at`) with every assignment (batch, order, vehicle, vehicle code,
and an explainable reason), flips assigned orders to `planned` and the used vehicles to
`assigned`, and keeps unassigned orders `pending`. The result is reproducible by due
time and the existing stable sort, survives restarts, and returns:

- `200` with `{batch_id, created_at, assignments, unassigned_order_ids, unassigned_reasons}`
- `409` when there are no confirmable orders or no available vehicles — no empty batch
  is written and no status changes
- `404` for an unknown batch id on `GET /api/dispatch/batches/{batch_id}`

`unassigned_reasons` maps each unassigned order id to why it could not be dispatched:
no available vehicle with enough remaining capacity, or no available vehicle carrying
the required license. The preview returns the same map, and committed batches persist
it so the batch detail reproduces it after restarts.

Concurrent commits serialize on a `BEGIN IMMEDIATE` write transaction; an order or
vehicle can never be confirmed twice.

