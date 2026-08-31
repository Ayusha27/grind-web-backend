# GRIND — PHP → FastAPI Migration Workflow

**Status:** Plan for review — no code written yet.
**Source:** `backend/` (PHP 8.1 + PDO/MySQL 5.7)
**Target:** `backend-v1/` (FastAPI, Python 3.14, uv)
**Consumer:** `frontend/` (React + Vite + axios, `VITE_API_BASE_URL=http://localhost:8000`)

---

## 0. What I found (audit summary)

### 0.1 Legacy backend — 32 files, 11 with real logic

| File | Type | Auth | DB tables touched |
|---|---|---|---|
| `api/workout.php` | JSON API | none (`client_id` from query) | workout_plans, workout_days, workout_exercises |
| `api/complete-workout.php` | JSON API | none | workout_logs |
| `api/validate_affiliate.php` | JSON API | none | affiliate_codes |
| `Payment/create_order.php` | JSON API | none | affiliate_codes + Razorpay Orders API |
| `Payment/verify_payment.php` | JSON API | none | enrollments |
| `Client/workout.php` | HTML page | `session_start()`, unchecked | workout_plans/days/exercises |
| `Client/complete-workout.php` | HTML redirect | none | workout_logs |
| `Base files/my-plan (1).php` | HTML page (2397 L) | access_token in query | clients, workout_*, diet_plans, workout_logs |
| `Base files/workout-progress (1).php` | HTML page | none (client_id hardcoded `1`) | workout_exercises, workout_logs, client_progress |
| `Base files/save-progress.php` | JSON API | none | workout_logs — **broken**, `require 'config.php'` does not exist |
| `Base files/start-your-journey (1).php` | HTML form | none | none — sends email via `mail()` |
| `admin/login (1).php` | HTML form | sets session | none — credentials hardcoded in source |
| `admin/dashboard.php` | HTML page | session | none |
| `admin/clients (1).php` | HTML + POST | session | clients |
| `admin/client-details.php` | HTML page | session | clients |
| `admin/create-plan.php` | HTML + POST | session | workout_plans, workout_days, workout_exercises |
| `admin/import-workout.php` | HTML + POST | **none** | workout_plans, workout_days, workout_exercises |
| `admin/add_diet.php` | HTML + POST | **none** | clients, diet_plans |
| `admin/add-progress.php` | HTML + POST | session | clients, client_progress |
| `admin/affiliate-dashboard.php` | HTML page | **none** | affiliate_codes ⟕ enrollments |
| `admin/create-client.php`, `admin/coupons.php` | **empty files** | — | — |
| `Base files/{about-grind,membership-guide,payment-success,enrollment}` | static/HTML markup | — | none (`enrollment` only echoes the Razorpay key) |

Duplicates to collapse: `config/database.php` ≡ `Base files/database.php`; `config/razorpay.php` ≡ `DB/razorpay.php`; `Client/*.php` is a pre-JSON draft of `api/*.php`.

### 0.2 Database — 11 MySQL tables

`clients`, `workout_plans`, `workout_days`, `workout_exercises`, `workout_logs`, `client_progress`, `diet_plans`, `enrollments`, `affiliate_codes`, `affiliate_conversions` *(schema exists, never written by any PHP file)*, `workout_progress` *(schema exists, empty, never referenced)*.

No foreign keys exist anywhere. Only `clients.email`, `clients.access_token`, and `affiliate_codes.code` are unique. `client_progress.client_id` is the only non-PK index.

### 0.3 backend-v1 — a scaffold, not an application

- `app/main.py` — **empty file**
- `app/core/config.py` — **empty file**
- `pyproject.toml` — fastapi, uvicorn[standard], sqlalchemy 2.x, **psycopg 3**, pydantic-settings
- `docker-compose.yml` — Postgres 17 only
- `.env.example` — Postgres vars only

**Reusable:** the dependency choice, the uv/pyproject setup, the compose file.
**Gaps:** everything else — no app, config, session, models, routers, schemas, middleware, migrations, tests, Dockerfile, or logging.

### 0.4 The engine mismatch (the one decision that shapes everything)

Legacy is **MySQL 5.7**. backend-v1 is scaffolded for **PostgreSQL 17**. This must be settled before any code is written — see §6, Decision 1.

---

## 1. Target architecture

```
backend-v1/
├─ app/
│  ├─ main.py                     app factory, lifespan, middleware, routers
│  ├─ core/
│  │  ├─ config.py                pydantic-settings (env-driven, no literals)
│  │  ├─ security.py              admin JWT, password hashing, Razorpay HMAC
│  │  ├─ logging.py               structured JSON logs + request id
│  │  └─ exceptions.py            AppError hierarchy + handlers
│  ├─ db/
│  │  ├─ session.py               async engine + session dependency
│  │  ├─ base.py                  DeclarativeBase
│  │  └─ models/                  11 ORM models, 1:1 with legacy tables
│  ├─ schemas/                    pydantic request/response models
│  ├─ repositories/               all SQL, one module per table
│  ├─ services/                   business rules ported from PHP
│  │  ├─ workout_service.py
│  │  ├─ progress_service.py
│  │  ├─ affiliate_service.py
│  │  ├─ payment_service.py
│  │  ├─ client_service.py
│  │  ├─ diet_service.py
│  │  └─ intake_service.py
│  ├─ api/
│  │  ├─ deps.py                  db session, admin guard, client-token guard
│  │  └─ v1/                      workout, progress, affiliate, payment,
│  │                              admin_*, client_portal, intake routers
│  └─ integrations/
│     ├─ razorpay_client.py       async httpx wrapper
│     └─ mailer.py                replaces PHP mail()
├─ migrations/                    alembic
├─ tests/
│  ├─ unit/                       parity rules PR-01..PR-32
│  └─ integration/                endpoint-level golden responses
├─ Dockerfile
├─ docker-compose.yml             app + db (+ optional nginx)
└─ MIGRATION_PLAN.md              this file
```

Layering rule: `api → services → repositories → db`. Routers contain no SQL and no business rules; services contain no HTTP concerns.

---

## 2. Endpoint mapping

Legacy paths are kept as **deprecated aliases** so `frontend/src/services/workoutService.ts` keeps working unchanged during cutover.

| Legacy | New | Method | Notes |
|---|---|---|---|
| `api/workout.php?client_id=` | `/api/v1/workout` | GET | alias: `/api/workout.php` |
| `api/complete-workout.php` | `/api/v1/workout/complete` | POST | accepts form **and** JSON |
| `api/validate_affiliate.php` | `/api/v1/affiliate/validate` | POST | alias kept |
| `Payment/create_order.php` | `/api/v1/payments/order` | POST | |
| `Payment/verify_payment.php` | `/api/v1/payments/verify` | POST | see Decision 2 |
| `Base files/save-progress.php` | `/api/v1/workout/logs` | POST | repairs the broken original |
| `Base files/my-plan (1).php?token=` | `/api/v1/portal/my-plan` | GET | returns the JSON the page inlined |
| `Base files/workout-progress (1).php` | `/api/v1/portal/progress` | GET | |
| `Base files/start-your-journey (1).php` | `/api/v1/intake` | POST | |
| `admin/login (1).php` | `/api/v1/admin/login` | POST | returns JWT |
| `admin/clients (1).php` | `/api/v1/admin/clients` | GET, POST | upsert-by-email |
| `admin/client-details.php` | `/api/v1/admin/clients/{id}` | GET | |
| `admin/create-plan.php` | `/api/v1/admin/plans` | POST | non-versioned legacy path |
| `admin/import-workout.php` | `/api/v1/admin/plans/import` | POST | versioned + transactional |
| `admin/add_diet.php` | `/api/v1/admin/diet` | POST | |
| `admin/add-progress.php` | `/api/v1/admin/progress` | POST | |
| `admin/affiliate-dashboard.php` | `/api/v1/admin/affiliates` | GET | |
| — | `/health`, `/health/db` | GET | new |

The HTML/CSS in the legacy pages is **not** migrated — `frontend/` already owns presentation. Only the data those pages computed is exposed as JSON.

---

## 3. Parity rules — ported verbatim

Each rule below is a quirk of the current system that I will reproduce exactly and cover with a test. Several are bugs; they stay unless you say otherwise (§6, Decision 4).

**Workout fetch**
- **PR-01** `client_id` defaults to `1` when the parameter is absent; a non-numeric value casts to `0` (PHP `(int)` semantics), not a 422.
- **PR-02** Active plan = `WHERE client_id=? AND is_active=1 ORDER BY id DESC LIMIT 1`.
- **PR-03** No plan → HTTP **200** with `{"success":true,"data":null,"message":"No workout plan assigned."}`.
- **PR-04** Days ordered by `day_number`; exercises by `sort_order`, nested under each day's `exercises` key.
- **PR-05** DB failure → 500 `{"success":false,"message":"Failed to fetch workout."}` (message text preserved).

**Workout completion**
- **PR-06** Required: `exercise_id > 0`, `day_id > 0`, non-empty `user_email`; otherwise 400 `"Missing required fields"`.
- **PR-07** `month_no`, `week_no`, `set_no` are hardcoded `1` and `completed` is hardcoded `1`.
- **PR-08** No existence check on `day_id`/`exercise_id`, no dedupe — repeat calls insert duplicate rows.
- **PR-09** Success → `{"success":true,"message":"Workout marked complete"}`.

**Affiliate**
- **PR-10** `validate` matches on `code = ? AND status='active'` and **deliberately does not check `expiry_date`** — an expired code validates here.
- **PR-11** Response shape is `{"success":true,"discount":<int>,"code":<str>}`; miss is `{"success":false,"message":"Coupon not found"}`, both at HTTP 200.
- **PR-12** Dashboard aggregate: `LEFT JOIN enrollments ON code = coupon_code AND payment_status='Paid'`, `COUNT(e.id)`, `COALESCE(SUM,0)`, `COALESCE(AVG,0)`, `commission_due = COALESCE(SUM(final_price)*commission_percent/100, 0)`, `ORDER BY revenue DESC`; grand totals summed in application code, not SQL.

**Payments**
- **PR-13** A coupon discounts **only** these three exact, case-sensitive plan names: `3 MONTH KICKSTART`, `6 MONTH TRANSFORMATION`, `12 MONTH LIFESTYLE EVOLUTION`. Any other plan pays full price even with a valid coupon.
- **PR-14** Order-time lookup **does** check expiry: `status='active' AND expiry_date >= CURDATE()`. (Contrast PR-10 — the two paths intentionally disagree, and the migration keeps that.)
- **PR-15** `final_price = price − (price × discount_percent / 100)`; `discount_percent` is integer-truncated from the DB.
- **PR-16** The `price <= 0` guard runs **after** `final_price` is computed and tests the *original* price, not the final one → `{"success":false,"message":"Price received is zero"}`.
- **PR-17** Razorpay order: `receipt = "GRIND_" + unix_seconds`, `amount = round(final_price × 100)` paise, `currency = "INR"`.
- **PR-18** Response returns rounded `amount` alongside the **unrounded** `final_price`.
- **PR-19** `verify_payment` writes the enrollment row with `payment_status` hardcoded `'Paid'` and does **not** touch `affiliate_conversions` or `affiliate_codes.total_sales/total_revenue`.
- **PR-20** Prices, plan name, and discount are taken from the client request and trusted as-is.

**Client & plan admin**
- **PR-21** Client upsert keys on `email`: existing → update `name`, `phone`, `goal` only, and the access token is **not** regenerated; new → insert then generate the token.
- **PR-22** Access token = `GR_` + first 3 chars of `strtoupper(str_pad(letters_only(name), 3, 'X'))` + `_` + zero-left-padded 6-digit id. `str_pad` pads on the **right**, so `"Al"` → `ALX`, `"Bo Xu"` → `BOX`, `""` → `XXX`.
- **PR-23** `create-plan` inserts with schema defaults (`is_active=1`, `version_no=1`), does **not** deactivate prior plans, and runs **without a transaction**.
- **PR-24** `import-workout` is the versioned path: `next_version = COALESCE(MAX(version_no),0)+1`, deactivate all plans for the client, insert with `is_active=1`, all inside one transaction that rolls back on any error.
- **PR-25** Import JSON key mapping: `plan_name`, `days[].day_name`, `days[].exercises[].{name,sets,reps,youtube}`; `notes` is written as the literal empty string; `day_number` and `sort_order` are 1-based positional indexes.
- **PR-26** `json_decode` falsiness is preserved: `null`, `[]`, `{}`, `0`, `"0"`, and `false` all yield `"Invalid JSON"`.
- **PR-27** Diet upsert resolves the client by `access_token` (not id), deactivates existing diet plans, then inserts `is_active=1` — and the original is **not** transactional.

**Client portal**
- **PR-28** Progress percentage divides by `COUNT(*)` of **every row in `workout_exercises` globally**, not the client's own plan: `round(distinct_completed / global_total × 100)`, `0` when the total is `0`. PHP `round()` is half-away-from-zero (`0.5 → 1`), which differs from Python's banker's rounding — a explicit helper will be used.
- **PR-29** Completed count = `COUNT(DISTINCT exercise_id) WHERE user_email=? AND completed=1`.
- **PR-30** Day payload keys and constants are reproduced exactly: `id` = `day_number`, `label` = `short` = `day_name`, `colorSoft` = `"rgba(255,92,53,.1)"`, `calMin` = 250, `calMax` = 350, `calNote` = `"Workout Day"`; exercises are remapped to `{name, sets, reps, note, yt}`.
- **PR-31** Day colour = `["#ff5c35","#2563eb","#16a34a","#9333ea","#ea580c","#0f766e"][(day_number − 1) % 6]`.
- **PR-32** Transformation stats: `current` = newest `client_progress` row, `start` = oldest; `weight_lost = start − current`, `waist_reduced = start − current`, both `0` unless **both** rows exist. Chart dates format as `"%d %b"` (e.g. `15 Jun`).

**Intake**
- **PR-33** Required fields are name, a `FILTER_VALIDATE_EMAIL`-valid email, age, weight. Sanitiser is `htmlspecialchars(strip_tags(trim(v)), ENT_QUOTES)`. Height renders as `"5ft 9in"` or `"175 cm"` depending on `height_unit`. Multi-select `goals`/`injuries` join with `", "`. The plain-text email body layout is reproduced line for line.

---

## 4. Phased workflow

Each phase ends in a reviewable, runnable state.

### Phase 1 — Foundation
1. Fill `app/core/config.py`: `Settings(BaseSettings)` — DB URL, CORS origins, admin credential hash, Razorpay key/secret, mail config, env name, log level. **No literal secrets anywhere in code.**
2. Rewrite `.env.example` with every variable; add real `.env` to `.gitignore` (already ignored).
3. `app/db/session.py` — async engine, `pool_pre_ping`, sized pool, `async_sessionmaker`, `get_db` dependency with commit/rollback/close.
4. `app/main.py` — `create_app()`, lifespan (engine warm-up + disposal), `/health` and `/health/db`.
5. Structured logging with a per-request correlation id.

### Phase 2 — Schema and data
6. Write the 11 SQLAlchemy models, matching legacy column names and types exactly (`tinyint(1)` → `Boolean`, `decimal` → `Numeric` with the same precision, `longtext` → `Text`).
7. Alembic baseline migration reproducing the legacy schema, **plus** the indexes the legacy schema lacks (`workout_days.plan_id`, `workout_exercises.day_id`, `workout_logs(user_email, completed)`, `workout_plans(client_id, is_active)`, `diet_plans(client_id, is_active)`, `enrollments.coupon_code`). Indexes change performance, never results.
8. A one-shot data-transfer script for the dumps in `grind sql/`, plus a row-count and checksum reconciliation report. *(Scope depends on Decision 1.)*

### Phase 3 — Repositories and services
9. Port every query one-for-one into `repositories/`, keeping ordering, `LIMIT`, and `COALESCE` semantics identical. All parameterised.
10. Port the business rules into `services/`, each function annotated with the `PR-xx` rules it implements.
11. Write the PHP-compat helpers once, in `app/core/compat.py`: `php_intval`, `php_floatval`, `php_round`, `php_str_pad`, `php_json_truthy`, `php_clean` — so PHP semantics live in one audited place instead of being re-derived per call site.

### Phase 4 — API surface
12. Pydantic schemas for every request and response, mirroring the legacy JSON envelope `{"success": bool, ...}` exactly.
13. Routers per §2, with the legacy-path alias router mounted alongside.
14. Auth dependencies: admin JWT guard, client access-token guard. Endpoints that legacy left unauthenticated (`import-workout`, `add_diet`, `affiliate-dashboard`) get the admin guard — see Decision 3.

### Phase 5 — Production hardening
15. Exception handlers producing the legacy error envelope; internal details logged, never returned.
16. Middleware: CORS restricted to configured origins, `TrustedHostMiddleware`, GZip, security headers, request-id, and rate limiting on `/payments/*`, `/affiliate/validate`, and `/admin/login`.
17. Async throughout: `asyncpg`/`aiomysql` for DB, `httpx.AsyncClient` for Razorpay, mail dispatched via a background task so intake submission does not block on SMTP.
18. `Dockerfile` (multi-stage, uv, non-root user), extended `docker-compose.yml`, uvicorn worker config, graceful shutdown.
19. OpenAPI docs enabled in dev, disabled in prod.

### Phase 6 — Verification
20. Unit tests: one per `PR-xx` rule, table-driven with the exact edge inputs named above.
21. Integration tests against a disposable DB seeded from `grind sql/`, asserting byte-comparable JSON for the five live API endpoints.
22. A differential harness: replay a fixed request corpus against both stacks and diff the responses. This is the concrete proof of the "identical inputs → identical outcomes" criterion.
23. `PARITY.md` recording every intentional deviation with its justification.

### Phase 7 — Cutover
24. Point `frontend/.env` at the FastAPI base URL; the `.php` aliases mean no frontend code has to change on day one.
25. Update `workoutService.ts` and add the missing service modules for the new endpoints.
26. Run both stacks in parallel, monitor, then retire `backend/`.

---

## 5. Security findings — needs action independent of this migration

1. **Live Razorpay key and secret are committed** in `backend/config/razorpay.php` and `backend/DB/razorpay.php` (`rzp_live_*`). Anyone with repo access can charge and refund against the production account. **Rotate in the Razorpay dashboard before cutover**, and load from env afterwards.
2. **Production DB credentials are committed** in `backend/config/database.php`. Rotate.
3. **Admin credentials are hardcoded in plaintext** in `admin/login (1).php`, compared with `==`. Replace with an env-held hash (Argon2/bcrypt) and constant-time comparison.
4. **Razorpay payment signature is never verified.** `verify_payment.php` receives `razorpay_signature` from the browser and ignores it; it also trusts `final_price` from the client. A forged POST creates a `'Paid'` enrollment for free. See Decision 2.
5. **Three admin endpoints have no auth at all** — `import-workout.php`, `add_diet.php`, `affiliate-dashboard.php`. The last leaks affiliate names, emails, revenue, and commissions to anyone with the URL.
6. **Reflected/stored XSS** across the admin pages (`echo $client['name']` unescaped). Moot once the API returns JSON only.
7. `.env` currently sits inside `backend-v1/` and is gitignored — confirm it was never committed to that repo's history.

None of these are business rules, so fixing them does not violate the parity requirement — but 4 and 5 change observable behaviour, so they are flagged as decisions rather than assumed.

---

## 6. Decisions I need from you

**Decision 1 — Database engine.** *This blocks Phase 2.*
- **(a) PostgreSQL** — matches the existing `backend-v1` scaffold (psycopg, compose). Requires converting the schema and the `grind sql/` dumps: `tinyint(1)`→`boolean`, `enum`→native enum, `AUTO_INCREMENT`→identity, `CURDATE()`→`CURRENT_DATE`, backtick→double-quote quoting, `utf8`→`UTF8`. Roughly a day of conversion plus reconciliation.
- **(b) MySQL 8** — zero schema conversion, dumps import as-is, lowest parity risk; means discarding the Postgres scaffold and swapping the driver to `asyncmy`/`aiomysql`.
- **My recommendation: (a) Postgres.** The scaffold already commits to it, the data volume is small (~930 exercise rows, 43 plans, 17 clients, 1 enrollment), and the conversion is mechanical and verifiable. Choose (b) if the production MySQL host must stay.

**Decision 2 — Razorpay signature verification.**
- **(a) Add it** — verify `HMAC_SHA256(order_id|payment_id, secret)` and reject mismatches; additionally re-derive `final_price` server-side instead of trusting the client. Closes the free-enrollment hole but **changes behaviour**: forged/mismatched requests that currently succeed will start failing.
- **(b) Strict parity** — port the hole as-is.
- **My recommendation: (a)**, gated behind `RAZORPAY_VERIFY_SIGNATURE=true` so you can flip it off if a legitimate flow depends on the current leniency. Signature checking is a payment-integrity control, not a business rule.

**Decision 3 — The three unauthenticated admin endpoints.**
- **(a)** Require admin auth on all of them (recommended — the affiliate dashboard in particular is a data leak).
- **(b)** Leave them open for parity.
- If (a): I need to know whether anything currently calls `import-workout.php` or `add_diet.php` unauthenticated (a cron job, a bookmark, an external tool), because that caller will break.

**Decision 4 — The three parity bugs.** PR-10 (expired coupons validate), PR-16 (zero-price guard tests the wrong variable), PR-28 (progress divides by the global exercise count, so every client's percentage is wrong and shrinks as other clients' plans are imported). Default per your brief is **preserve all three verbatim**. Confirm, or tell me which to fix — each is a one-line change plus a test flip.

**Decision 5 — Hardcoded identities.** `my-plan` computes progress for a hardcoded email and `workout-progress` uses hardcoded `client_id = 1`. These cannot be preserved literally in a multi-client API. I intend to derive both from the access token in the request, and note it in `PARITY.md`. Confirm.

**Decision 6 — Intake email transport.** PHP `mail()` has no direct Python equivalent. I'll use SMTP via env config (host, port, user, password, from, to). Tell me the SMTP provider, or I'll build it provider-agnostic and leave the credentials for you.

---

## 7. Acceptance criteria → how each is proven

| Criterion | Evidence |
|---|---|
| Full feature parity | §2 mapping covers all 19 non-empty legacy entry points; §3 catalogues 33 behavioural rules; each has a test |
| Identical outcomes for identical inputs | Phase 6 differential harness diffing both stacks over a fixed request corpus; deviations enumerated in `PARITY.md` |
| Production best practice | Phases 1 & 5: env-driven config, structured logging, health checks, exception handling, CORS/host/rate-limit middleware, JWT admin auth, Docker, graceful shutdown, migrations |
| DB fully decoupled from PHP | No PHP in the request path; async pooled SQLAlchemy 2.x; every query parameterised; Alembic-managed schema; reconciliation report from the transfer script |

---

## 8. Suggested order of execution

Phases 1–2 are prerequisites for everything. Phases 3–4 proceed table-by-table in this order, each slice testable end to end:

1. **workout read path** — the only endpoint the React frontend calls today
2. **workout logging** — completion + logs
3. **affiliate** — validate + dashboard
4. **payments** — order + verify (highest risk; do it once Decision 2 is settled)
5. **client portal** — my-plan + progress
6. **admin** — clients, plans, import, diet, progress
7. **intake** — email path

I'll start on Phase 1 as soon as Decision 1 is settled; Decisions 2–6 are only needed by the time their slice comes up.
