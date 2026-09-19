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
- `GET /api/vehicles` and `POST /api/vehicles`
- `POST /api/dispatch/plan` — read-only deterministic preview for pending orders and available vehicles; never mutates state
- `POST /api/dispatch/commit` — recomputes the same plan inside one database transaction and confirms it as a persistent batch
- `GET /api/dispatch/batches` — batch summaries ordered by creation time and batch id
- `GET /api/dispatch/batches/{batch_id}` — full confirmed result for one batch (404 if unknown)

The baseline planner is deterministic and local. It assigns only pending orders whose weight fits an available vehicle and whose driver holds the order's required license; it does not call external maps or use live customer data.

### Driver capabilities and required licenses

Orders may carry an optional `required_license` (omitted or `null` means none;
otherwise a 1–32 character credential code). A vehicle is described by `code`,
`capacity_kg`, a non-empty `driver_name`, and a de-duplicated `licenses` array.
`POST /api/vehicles` creates a vehicle with those capabilities; `GET /api/vehicles`
returns them for every vehicle. The seeded demo vehicles ship with drivers and
license data. When an order declares a required license, only an available vehicle
whose `licenses` include it is a candidate; capacity and the tightest-fit tie-break
work exactly as before.

Every plan and confirmed result keeps `unassigned_order_ids` and adds
`unassigned_reasons`, a map keyed by unassigned order id explaining why it could
not be dispatched — a capacity shortfall ("no available vehicle can carry … kg")
or a missing credential ("no available vehicle holds the required license '…'").
The reasons are persisted with the batch and returned identically from the batch
detail endpoint, including after a restart.

### Confirming dispatch

`POST /api/dispatch/commit` takes no request body. Within one SQLite transaction it
recomputes the plan with the exact same rule as the preview, then persists a batch
(`batch_id`, `created_at`) with every assignment (batch, order, vehicle, vehicle code,
and an explainable reason), flips assigned orders to `planned` and the used vehicles to
`assigned`, and keeps unassigned orders `pending` together with their reasons. The
result is reproducible by due time and the existing stable sort, survives restarts,
and returns:

- `200` with `{batch_id, created_at, assignments, unassigned_order_ids, unassigned_reasons}`
- `409` when there are no confirmable orders, no available vehicles, or no feasible
  assignment (including a capability-only mismatch that leaves zero assignments) — no
  empty batch is written and no status changes
- `404` for an unknown batch id on `GET /api/dispatch/batches/{batch_id}`

Concurrent commits serialize on a `BEGIN IMMEDIATE` write transaction; an order or
vehicle can never be confirmed twice.

