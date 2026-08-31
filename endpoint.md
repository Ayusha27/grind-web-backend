# GRIND API — Endpoint Reference

All 17 v1 endpoints, the 7 legacy `.php` aliases, and 2 ops probes.

Base prefix: `/api/v1` (configurable via `API_V1_PREFIX`).
Interactive docs while the server runs: <http://127.0.0.1:8000/docs>

---

## Running it

### 1. Infrastructure

```bash
cd backend-v1
docker compose up -d postgres redis
```

> **Windows note:** if you have a native PostgreSQL service installed, it will
> hold port 5432 and shadow the container — every connection then fails with
> `password authentication failed`. Stop it first:
> `Stop-Service postgresql-x64-16, postgresql-x64-18`

### 2. Dependencies and schema

```bash
uv sync                 # installs into .venv
uv run alembic upgrade head
```

### 3. Start the server

```bash
uv run python scripts/dev.py        # -> http://127.0.0.1:8000
```

Use this rather than calling `uvicorn` directly. On Windows uvicorn builds a
`ProactorEventLoop`, which psycopg's async mode cannot run on — the app dies
during startup on its first database connection. `scripts/dev.py` selects a
compatible loop explicitly. On Linux it is a plain uvicorn run.

If you want auto-reload while editing:

```bash
uv run uvicorn app.main:app --reload --port 8000
```

(`--reload` happens to work on Windows because uvicorn picks a selector loop
when it runs a subprocess. Convenient, but not something to depend on.)

### 4. Verify

```bash
curl http://127.0.0.1:8000/health/ready
# {"status":"ok","checks":{"database":"ok","redis":"ok"}}
```

### Production

```bash
docker compose up -d --build      # gunicorn, 4 workers, behind deploy/nginx.conf
```

### Test and check

```bash
uv run pytest tests/unit tests/integration -q
uv run ruff check app tests scripts migrations
uv run mypy app
uv run python scripts/reconcile.py       # legacy row counts still intact
```

---

## Conventions that apply everywhere

**Response envelope.** Every endpoint returns `{"success": bool, ...}`. Errors
add `"message"`. These strings are byte-for-byte what the PHP returned — they
are asserted in `tests/unit/test_parity_rules.py`, so treat them as API surface,
not as prose to tidy up.

**Request bodies.** Every POST accepts *either* JSON or form-encoded input,
decided by `Content-Type` (`app/api/deps.py::body_params`). The legacy frontend
posts forms; the SPA posts JSON. Repeated keys like `goals[]` collapse to a list.

**PHP-typed output.** Booleans come back as `1`/`0` and timestamps as
`"2026-07-12 04:18:39"`, because that is what `json_encode` produced and the
frontend parses it. Key order is preserved for the same reason.

**Lenient coercion.** Legacy endpoints never return 422. `client_id=abc`
becomes `0` via a PHP `(int)` cast rather than a validation error.

**Rate limits** are per-IP, backed by Redis. If Redis is unreachable the limiter
fails *open* (logs `rate_limit_unavailable` and allows the request) — availability
was chosen over enforcement here.

| Bucket | Limit | Applies to |
|---|---|---|
| `login` | 5 / 5 min | admin login |
| `payment` | 20 / min | order + verify |
| `coupon` | 30 / min | coupon validation |
| `intake` | 5 / 10 min | intake form |
| `write` | 120 / min | set-ticking, log writes |

**Authentication.** Two separate schemes:

- *Admin* — `Authorization: Bearer <jwt>` from `/admin/login`. 8-hour expiry.
- *Client portal* — an opaque `access_token` from the `clients` table, passed as
  `?token=…` (bookmarked legacy links) or as a Bearer header (the SPA).

---

## Workout

### `GET /api/v1/workout`

The highest-traffic endpoint in the app. Returns a client's active plan with its
days and exercises nested inside.

| Query | Type | Notes |
|---|---|---|
| `client_id` | int-ish | Optional. Absent or empty → `1`. Non-numeric → `0`. |

Returns the newest plan where `is_active` is true, its days ordered by
`day_number`, and each day's exercises ordered by `sort_order` (nulls first),
then `id`. Served from Redis for 60s, keyed on the *resolved* `client_id` — so
`?client_id=abc` and `?client_id=0` correctly share one entry.

A client with no plan is **not** a 404. It is `200` with
`{"success":true,"data":null,"message":"No workout plan assigned."}`. Note that
the success branch has no `message` key at all; that asymmetry is deliberate.

### `POST /api/v1/workout/complete`

Ticks one set as done. Rate limit: `write`.

| Field | Required | Notes |
|---|---|---|
| `exercise_id` | yes | must resolve `> 0` |
| `day_id` | yes | must resolve `> 0` |
| `user_email` | yes | must be non-empty after trim |

Anything missing → `{"success":false,"message":"Missing required fields"}`.

Two behaviours worth knowing before you build on this: `month_no`, `week_no`,
`set_no` and `completed` are **hardcoded** to `1,1,1,true` in the insert and are
ignored even if you send them; and there is no dedupe or foreign-key check, so
calling it twice writes two rows. Both are faithful to the original.

### `POST /api/v1/workout/logs`

The full-fidelity log write — what `save-progress.php` was meant to be. The
original was dead code (it required a `config.php` that does not exist in the
repo, so every call was a PHP fatal), so there was no behaviour to preserve,
only intent.

| Field | Required | Default |
|---|---|---|
| `email` | yes | — |
| `month`, `week`, `set` | no | `1` |
| `day`, `exercise` | no | `0` |
| `completed` | no | falsy |

Unlike `/workout/complete`, this one honours everything you send it.

---

## Affiliate

### `POST /api/v1/affiliate/validate`

Checks a coupon code. Rate limit: `coupon` — this is a code-enumeration oracle,
which is why it is throttled.

Body `{"code": "GR_ARU_10"}` → `{"success":true,"discount":10,"code":"GR_ARU_10"}`

A miss returns `{"success":false,"message":"Coupon not found"}` at **HTTP 200**,
not 404.

This endpoint deliberately does **not** check expiry. An expired code validates
here and is then refused at order time — inconsistent, and exactly what the
legacy did. `LEGACY_AFFILIATE_SKIP_EXPIRY=false` changes it if you want it fixed.

### `GET /api/v1/admin/affiliates` — admin

Affiliate dashboard: every code with its sales, revenue and commission, plus
totals. Totals are summed in Python rather than SQL, reproducing the original's
`foreach` arithmetic exactly.

**This route was public in the legacy system**, exposing affiliate names, emails
and revenue to anyone who found the URL. It is admin-only now.

---

## Payments

### `POST /api/v1/payments/order`

Creates a Razorpay order. Rate limit: `payment`.

| Field | Notes |
|---|---|
| `plan` | plan name |
| `price` | original price, PHP `floatval` semantics |
| `coupon` | optional |

A discount applies only when the coupon is non-empty **and** the plan is one of
`3 MONTH KICKSTART`, `6 MONTH TRANSFORMATION`, `12 MONTH LIFESTYLE EVOLUTION`.
Unlike `/affiliate/validate`, this path *does* enforce expiry.

Two quirks preserved on purpose:

- Arithmetic is float, not `Decimal`. Switching would shift paise at half-unit
  boundaries and disagree with amounts Razorpay already has on file.
- The zero-price guard tests the **original** price, after the discount is
  computed. A 100% coupon therefore passes the check and creates a zero-amount
  order. Set `LEGACY_ZERO_PRICE_CHECKS_ORIGINAL=false` to test the final price.

A rejected price returns `{"success":false,"message":"Price received is zero"}`
at HTTP 200. Receipt is `GRIND_<unix seconds>`; amount is in paise.

### `POST /api/v1/payments/verify`

Confirms a payment and writes the enrollment. Rate limit: `payment`.

Expects the Razorpay triplet (`razorpay_order_id`, `razorpay_payment_id`,
`razorpay_signature`) plus the customer and pricing fields.

**This is the one place the rewrite is deliberately stricter than the original.**
The PHP received `razorpay_signature` and ignored it, so a hand-crafted POST
created a `Paid` enrollment for free. The signature is now verified
(HMAC-SHA256). The frontend already sends it, so no legitimate flow breaks.
`RAZORPAY_VERIFY_SIGNATURE=false` restores the old behaviour if you need it.

Prices are still taken from the request rather than recomputed — changing that
would change outcomes, so it is off by default. A mismatch against the
server-side calculation is **logged**, which gives you the evidence to enable
`PAYMENTS_RECOMPUTE_PRICE=true` safely.

---

## Client portal

Both routes need a client access token.

### `GET /api/v1/portal/my-plan`

The client's own plan, plus diet. Cached 60s per access token — this page gets
reopened constantly during a workout and only changes when an admin republishes.

### `GET /api/v1/portal/progress`

Completed-exercise count and body measurements over time.

Explicitly **not cached**: the count has to move the instant a set is ticked or
the UI looks broken.

The progress denominator is the *global* exercise count, not the client's own
plan length — so it shifts for everyone whenever any plan is imported. That is
the legacy behaviour (`LEGACY_GLOBAL_PROGRESS_DENOMINATOR`).

An unknown token returns `{"success":false,"message":"Invalid Access Link"}`,
matching the PHP's `die()` string.

---

## Intake

### `POST /api/v1/intake`

The "start your journey" form. Rate limit: `intake` (5 per 10 min — it sends
mail, so it is a spam vector).

Required: `name`, `email`, `age`, `weight`. Errors are collected and joined into
one message, in the PHP's original field order. Email uses `email-validator`
with deliverability checks off, so no DNS lookup can stall the request.

Validation is synchronous — the user must see field errors — but the SMTP send
is a background task, so the response returns in ~5 ms instead of ~800 ms and
never holds a concurrency slot on a network wait. The email body is reproduced
line for line, including the `N/A` / `None` / `None selected` fallbacks, because
a human reads these by eye.

---

## Admin

All routes below require `Authorization: Bearer <jwt>` unless
`LEGACY_OPEN_ADMIN=true`.

### `POST /api/v1/admin/login`

Rate limit: `login`. Body `{"username", "password"}` → a JWT plus `expires_in`.

Replaces a plaintext `==` comparison with argon2. Bad username and bad password
return the *same* message (`"Invalid Login"`) so the endpoint cannot be used to
enumerate usernames, and the username is compared with `compare_digest` to close
the timing channel.

### `GET /api/v1/admin/clients`

All clients with their access tokens.

### `POST /api/v1/admin/clients`

Create or update a client, matched on email. Requires `email`; `name`, `phone`,
`goal` optional. New clients get a generated access token.

### `GET /api/v1/admin/clients/{client_id}`

One client with their plans, diet and measurement history.

### `POST /api/v1/admin/plans`

Publishes a plan the non-versioned way. Requires `client_id`, `plan_name`,
`workout_json`. Deactivates existing plans, inserts the new one, drops that
client's caches.

### `POST /api/v1/admin/plans/import`

The versioned, transactional path. Requires `client_id` and `workout_json`.
Assigns `MAX(version_no) + 1`, parses the JSON into `workout_days` and
`workout_exercises`, and commits all of it in one transaction — a malformed plan
leaves nothing behind.

Also invalidates the **global** exercise-count cache, because importing changes
the progress denominator for every client, not just this one.

### `POST /api/v1/admin/diet`

Saves a diet plan against a client's `access_token`. Requires `access_token` and
`diet_json`.

### `POST /api/v1/admin/progress`

Records a measurement row. Requires `client_id`; `weight`, `waist`, `chest`,
`arms`, `thighs`, `notes` all optional.

---

## Legacy `.php` aliases

Seven routes kept so the existing frontend and any bookmarked links keep working.
They are **unprefixed**, hidden from OpenAPI, and delegate to the same services
as their v1 counterparts — no duplicated logic.

| Legacy path | Successor |
|---|---|
| `GET /api/workout.php` | `/api/v1/workout` |
| `POST /api/complete-workout.php` | `/api/v1/workout/complete` |
| `POST /api/validate_affiliate.php` | `/api/v1/affiliate/validate` |
| `POST /Payment/create_order.php` | `/api/v1/payments/order` |
| `POST /Payment/verify_payment.php` | `/api/v1/payments/verify` |
| `POST /save-progress.php` | `/api/v1/workout/logs` |
| `POST /start-your-journey.php` | `/api/v1/intake` |

Each sends `Deprecation: true` and a `Link: <successor>; rel="successor-version"`
header, and logs `legacy_path_used`. Grep that in the logs to find out when the
module is safe to delete:

```bash
docker compose logs api | grep legacy_path_used
```

Responses are byte-identical to the v1 routes — verified by
`test_legacy_php_alias_is_identical`.

---

## Ops

### `GET /health`

Liveness. Touches nothing external, deliberately: if this checked the database,
a brief DB blip would make the orchestrator kill every healthy pod, turning a
10-second hiccup into a full outage.

### `GET /health/ready`

Readiness. Pings Postgres and Redis.

```json
{"status":"ok","checks":{"database":"ok","redis":"ok"}}
```

---

## Quick reference

| Method | Path | Auth | Limit |
|---|---|---|---|
| GET | `/api/v1/workout` | — | — |
| POST | `/api/v1/workout/complete` | — | write |
| POST | `/api/v1/workout/logs` | — | write |
| POST | `/api/v1/affiliate/validate` | — | coupon |
| POST | `/api/v1/payments/order` | — | payment |
| POST | `/api/v1/payments/verify` | — | payment |
| GET | `/api/v1/portal/my-plan` | client | — |
| GET | `/api/v1/portal/progress` | client | — |
| POST | `/api/v1/intake` | — | intake |
| POST | `/api/v1/admin/login` | — | login |
| GET | `/api/v1/admin/clients` | admin | — |
| POST | `/api/v1/admin/clients` | admin | — |
| GET | `/api/v1/admin/clients/{id}` | admin | — |
| GET | `/api/v1/admin/affiliates` | admin | — |
| POST | `/api/v1/admin/plans` | admin | — |
| POST | `/api/v1/admin/plans/import` | admin | — |
| POST | `/api/v1/admin/diet` | admin | — |
| POST | `/api/v1/admin/progress` | admin | — |
| GET | `/health`, `/health/ready` | — | — |
