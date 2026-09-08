# GRIND Backend — Endpoint & File Workflow Map

**What this document is.** A map of every HTTP endpoint in this codebase, the file
each one lives in, and every other file it touches on the way to a response. If you
are new here, read sections 1–3 and you will be able to explain the system to
someone else. If you already know the system, section 6 (the shared-file matrix)
and section 7 (cross-endpoint data flow) are the reference tables.

**The system in one sentence.** A FastAPI backend for a fitness coaching business:
prospects submit an intake form and pay through Razorpay, an admin builds workout
and diet plans for them, and clients open a personal portal link to see their plan
and tick off exercises.

**Counts.** 18 modern endpoints under `/api/v1`, 7 deprecated `.php` aliases that
reuse the exact same code, and 2 operational health probes. 27 routes total.

---

## 1. How a request travels (read this first)

Every single request — modern or legacy — goes through the same five layers. Each
layer has one job, and it only talks to the layer directly below it.

```
        HTTP request
             │
             ▼
┌───────────────────────────────────────────────────────────────────┐
│ 1. MIDDLEWARE          app/middleware.py, app/main.py             │
│    Adds a request id, security headers, CORS, gzip.               │
│    Nothing here knows what a "workout" is.                        │
└───────────────────────────────────────────────────────────────────┘
             │
             ▼
┌───────────────────────────────────────────────────────────────────┐
│ 2. ROUTER              app/api/v1/*.py  or  app/api/legacy.py     │
│    Declares the URL. Reads the body/query. Applies the guards.    │
│    Contains NO business rules — it delegates immediately.         │
└───────────────────────────────────────────────────────────────────┘
             │
             ├──► GUARDS (run before the handler body):
             │      app/api/deps.py          who is calling? (auth)
             │      app/cache/rate_limit.py  are they calling too often?
             ▼
┌───────────────────────────────────────────────────────────────────┐
│ 3. SERVICE             app/services/*.py                          │
│    ALL business rules live here: pricing, validation, JSON        │
│    parsing, PHP-compatible number handling. No SQL, no HTTP.      │
└───────────────────────────────────────────────────────────────────┘
             │
             ▼
┌───────────────────────────────────────────────────────────────────┐
│ 4. REPOSITORY          app/repositories/*.py                      │
│    The only place SQL is written. Returns model objects.          │
└───────────────────────────────────────────────────────────────────┘
             │
             ▼
┌───────────────────────────────────────────────────────────────────┐
│ 5. DATA & OUTSIDE WORLD                                           │
│    PostgreSQL (app/db/)   Redis (app/cache/)                      │
│    Razorpay + SMTP (app/integrations/)                            │
└───────────────────────────────────────────────────────────────────┘
```

**Two rules that explain most of the file layout:**

1. A router never writes SQL, and a repository never makes a business decision.
   If you are hunting for "why is the price 20% off?", it is in `app/services/`.
   If you are hunting for "which rows does it read?", it is in `app/repositories/`.
2. **No endpoint ever calls another endpoint over HTTP.** They connect in two
   quieter ways instead: they *share service and repository files*, and they
   *hand data to each other through the database* (section 7). This matters — you
   will not find a single `httpx.get("/api/v1/...")` anywhere in the app.

### Vocabulary used below

| Term | Plain meaning |
|---|---|
| **Router** | The file that says "this URL runs this function". |
| **Service** | Business-rules file. Decides *what* should happen. |
| **Repository** | Database-access file. Decides *which rows* to read or write. |
| **Dependency / guard** | A small function FastAPI runs *before* the handler — here, auth checks and rate limits. |
| **Access token (`GR_ALP_009001`)** | A client's permanent portal password-in-a-link. Generated once when the client is created. |
| **JWT** | The short-lived admin login token, sent as `Authorization: Bearer …`. |
| **PHP-compat helper** | A function in `app/core/compat.py` that reproduces an old PHP quirk exactly (e.g. `"abc"` becomes `0`, not an error). This backend replaces a PHP system and must return identical answers. |

---

## 2. The five modules at a glance

```
 ┌──────────────┐   coupon code    ┌──────────────┐
 │  1. INTAKE   │ ················ │ 2. AFFILIATE │
 │  lead form   │                  │   coupons    │
 └──────┬───────┘                  └──────┬───────┘
        │ email to staff                  │ discount %
        ▼                                 ▼
   (human signs                    ┌──────────────┐
    the client up)                 │ 3. PAYMENTS  │
        │                          │   Razorpay   │
        │                          └──────┬───────┘
        │                                 │ enrollment row
        ▼                                 ▼
 ┌─────────────────────────────────────────────────┐
 │ 4. ADMIN — creates the client, writes the       │
 │    workout plan, diet plan, and measurements    │
 └───────────────┬─────────────────────────────────┘
                 │ writes rows + clears cache
                 ▼
 ┌─────────────────────────────────────────────────┐
 │ 5. WORKOUT / PORTAL — what the client reads     │
 │    and what the client ticks off                │
 └─────────────────────────────────────────────────┘
```

| # | Module | Router file | Who calls it |
|---|---|---|---|
| 1 | Intake | `app/api/v1/intake.py` | Public marketing site |
| 2 | Affiliate | `app/api/v1/affiliate.py` | Public checkout page + admin |
| 3 | Payments | `app/api/v1/payments.py` | Public checkout page |
| 4 | Admin | `app/api/v1/admin.py` | Coach's admin panel (needs login) |
| 5 | Workout & Portal | `app/api/v1/workout.py`, `app/api/v1/portal.py` | The client's browser |
| — | Legacy aliases | `app/api/legacy.py` | Old PHP frontend, bookmarks, cron |
| — | Ops | `app/main.py` | Load balancer / Kubernetes |

---

## 3. Module walkthroughs

Each block below shows the endpoint, the file it is declared in, and every file it
reaches. Read the arrows top to bottom — that is execution order.

### 3.1 Intake — "someone filled in the contact form"

```
POST /api/v1/intake                                app/api/v1/intake.py
POST /start-your-journey.php   (legacy alias)      app/api/legacy.py
  │
  ├─ guard   limit_intake ──────────────► app/cache/rate_limit.py  (5 per 10 min)
  ├─ parse   BodyParams ────────────────► app/api/deps.py   accepts form OR JSON
  │
  ├─► app/services/intake_service.py     build_intake_email()
  │      ├─► app/core/compat.py          php_clean / php_trim
  │      ├─► email_validator             (3rd-party) checks the address
  │      └─► app/core/exceptions.py      ValidationFailure → HTTP 400
  │
  └─► background task ─► app/integrations/mailer.py  send_intake_email()
                            └─► SMTP server
```

**The one thing to understand here:** validation is **synchronous** (the visitor
must see "Age is required" immediately), but the email send is a **background
task**. The response returns in about 5 ms; the SMTP conversation finishes
afterwards. Because the reply has already been sent, a mail failure can only be
logged — never returned. This endpoint **touches no database and no cache.**

### 3.2 Affiliate — coupon codes

```
POST /api/v1/affiliate/validate                    app/api/v1/affiliate.py
POST /api/validate_affiliate.php  (legacy)         app/api/legacy.py
  │
  ├─ guard  limit_coupon ─► app/cache/rate_limit.py  (30/min — this endpoint is
  │                                             a coupon-guessing oracle)
  └─► app/services/affiliate_service.py  validate_code()
         ├─► app/core/compat.py            php_trim
         └─► app/repositories/affiliate_repo.py  get_active_code()
                └─► table: affiliate_codes

GET /api/v1/admin/affiliates                       app/api/v1/affiliate.py
  │
  ├─ guard  AdminUser ─────► app/api/deps.py → app/core/security.py (JWT)
  └─► app/services/affiliate_service.py  get_dashboard()
         └─► app/repositories/affiliate_repo.py  get_dashboard_rows()
                └─► tables: affiliate_codes JOIN enrollments
```

**Deliberate quirk worth knowing:** `validate` does **not** check the coupon's
expiry date, but `/payments/order` **does**. A code can therefore say "valid" on
the checkout page and still be refused at payment time. That is inherited PHP
behaviour, kept on purpose and documented in the service file.

**Security note:** the dashboard was public in the old system and leaked affiliate
names, emails and revenue. It is now admin-only.

### 3.3 Payments — Razorpay checkout, two steps

```
STEP 1 ── POST /api/v1/payments/order              app/api/v1/payments.py
          POST /Payment/create_order.php (legacy)  app/api/legacy.py
  │
  ├─ guard  limit_payment ─► app/cache/rate_limit.py  (20/min — each call
  │                                     costs a real request to Razorpay)
  └─► app/services/payment_service.py  create_order()
         ├─► app/core/compat.py      php_floatval / php_intval / php_round_int
         ├─► app/repositories/affiliate_repo.py  get_active_unexpired_code()
         │      └─► table: affiliate_codes          ← the coupon lookup
         ├─► app/core/config.py      DISCOUNT_ELIGIBLE_PLANS gate, LEGACY_* flags
         └─► app/integrations/razorpay_client.py  create_order()
                └─► HTTPS → api.razorpay.com

     returns → { order_id, amount, final_price, discount_percent }
                        │
                        │  the browser opens the Razorpay widget, the customer
                        │  pays, and Razorpay hands back a signature
                        ▼
STEP 2 ── POST /api/v1/payments/verify              app/api/v1/payments.py
          POST /Payment/verify_payment.php (legacy) app/api/legacy.py
  │
  ├─ guard  limit_payment ─► app/cache/rate_limit.py
  └─► app/services/payment_service.py  verify_payment()
         ├─► app/core/security.py     verify_razorpay_signature()  ← proves the
         │                              payment is real, not a forged POST
         ├─► app/repositories/affiliate_repo.py   (re-price check, log-only)
         ├─► app/repositories/enrollment_repo.py  get_by_payment_id()  duplicate check
         └─► app/repositories/enrollment_repo.py  insert()
                └─► table: enrollments  (payment_status = 'Paid')
```

**Why two endpoints:** money is never taken by this backend. Step 1 asks Razorpay
to open an order; the customer pays inside Razorpay's widget; step 2 records the
result. The `order_id` produced by step 1 is carried by the browser into step 2 —
that is the single most important piece of data passed between two endpoints in
this system (see section 7).

**Retry rule in `razorpay_client.py`:** only a *connection* failure is retried
(the request provably never arrived). A timeout or a 5xx is **not** retried,
because Razorpay does not de-duplicate and a retry could create a second order.

### 3.4 Admin — the coach's control panel

Every route below is under `/api/v1/admin` in `app/api/v1/admin.py`, and every one
except `/login` requires the JWT that `/login` returns.

```
POST /admin/login
  ├─ guard limit_login (5 per 5 min) ─► app/cache/rate_limit.py
  └─► app/core/security.py  verify_admin_credentials() → create_access_token()
        returns the JWT that unlocks everything below
        (the failure message is identical for a bad username and a bad
         password, so it cannot be used to discover valid usernames)

GET  /admin/clients            ─► client_service.list_clients
POST /admin/clients            ─► client_service.upsert_client   ★ mints access_token
GET  /admin/clients/{id}       ─► client_service.get_client
        all three ─► app/repositories/client_repo.py ─► table: clients

POST /admin/plans              ─► plan_service.create_plan       (unversioned)
POST /admin/plans/import       ─► plan_service.import_plan       (versioned, atomic)
        both ─► app/repositories/workout_repo.py
                 ─► tables: workout_plans, workout_days, workout_exercises
        both ─► app/cache/redis.py  invalidate_client_caches()   ★ see below

POST /admin/diet               ─► diet_service.save_diet_plan
        ─► app/repositories/client_repo.py   (find client BY ACCESS TOKEN)
        ─► app/repositories/diet_repo.py     ─► table: diet_plans
        ─► app/cache/redis.py  invalidate_client_caches()        ★

POST /admin/progress           ─► client_service.add_progress
        ─► app/repositories/client_repo.py   ─► table: client_progress
```

Request shapes for these routes are strict Pydantic models in
`app/schemas/admin.py` — unlike the public routes, which accept loose form data.

**★ Two things to remember about admin writes:**

- **`POST /admin/clients` is where a client's `access_token` is born.** The
  algorithm lives in `client_service.build_access_token()`: three letters of the
  name (padded with `X`) plus the zero-padded id, giving `GR_ALP_009001`. Updating
  an existing client never regenerates it — that would break every portal link the
  client has bookmarked.
- **Plan and diet writes clear the Redis cache** via `invalidate_client_caches()`.
  Without that call, a client would keep seeing their old plan for up to 60
  seconds. This is the mechanism that connects admin endpoints to client
  endpoints — they never call each other, they just share cache keys.

**`create_plan` vs `import_plan` — a real difference, not a duplicate:**

| | `POST /admin/plans` | `POST /admin/plans/import` |
|---|---|---|
| Deactivates the old plan | No — the client ends up with two active plans | Yes |
| Version number | Column default (1) | Incremented (`get_max_version` + 1) |
| Exercise `notes` when unset | `NULL` | empty string `""` (visible to the client) |
| Clears the portal cache key | No (client id only) | Yes (looks up the access token first) |

### 3.5 Workout & Portal — what the client sees

```
GET /api/v1/workout          ?client_id=1          app/api/v1/workout.py
GET /api/workout.php         (legacy)              app/api/legacy.py
  │
  ├─► app/core/compat.py  php_intval    "abc" → 0, missing → 1 (not an error)
  ├─► app/cache/redis.py  workout_key(id) → cache_get_json()   ── HIT? return now
  ├─► app/services/workout_service.py  get_workout()
  │      └─► app/repositories/workout_repo.py get_active_plan + get_days_with_exercises
  │             └─► tables: workout_plans, workout_days, workout_exercises
  └─► app/cache/redis.py  cache_set_json(60s)

POST /api/v1/workout/complete                      app/api/v1/workout.py
POST /api/complete-workout.php  (legacy)           app/api/legacy.py
  ├─ guard limit_write (120/min — ticking sets is bursty)
  └─► app/services/workout_service.py complete_workout()
         └─► app/repositories/workout_repo.py insert_log()  ─► table: workout_logs

POST /api/v1/workout/logs                          app/api/v1/workout.py
POST /save-progress.php        (legacy)            app/api/legacy.py
  ├─ guard limit_write
  └─► app/services/workout_service.py save_log()   ─► table: workout_logs
```

```
GET /api/v1/portal/my-plan   ?token=GR_ALP_009001  app/api/v1/portal.py
  │
  ├─ guard  CurrentClient ─► app/api/deps.py require_client()
  │            └─► app/repositories/client_repo.py get_by_access_token()
  │                  (the token may arrive as ?token= for old bookmarked links
  │                   OR as an Authorization: Bearer header for the SPA)
  ├─► app/cache/redis.py  portal_key(token) → cached? return
  ├─► app/services/portal_service.py get_my_plan()
  │      ├─► workout_repo   the active plan, its days and exercises
  │      ├─► diet_repo      the active diet plan (raw JSON string)
  │      ├─► client_repo    the client row
  │      └─► app/cache/redis.py cached_exercise_count()  ← progress denominator
  └─► app/cache/redis.py  cache_set_json(60s)

GET /api/v1/portal/progress  ?token=GR_ALP_009001  app/api/v1/portal.py
  ├─ guard  CurrentClient  (same as above)
  └─► app/services/portal_service.py get_progress()
         ├─► workout_repo.count_completed_exercises()
         ├─► client_repo.get_progress_history()  ─► table: client_progress
         └─► NOT CACHED — on purpose
```

**Why one portal route is cached and the other is not.** `my-plan` is opened
repeatedly during a workout and only changes when the coach republishes, so a
60 second cache is free. `progress` must move the instant a set is ticked;
caching it would make the UI look broken.

**A known, deliberately preserved bug.** The progress percentage divides by
`COUNT(*)` of the **entire** `workout_exercises` table, not the client's own plan.
Every client's percentage therefore shrinks whenever any other client's plan is
imported. It is inherited PHP behaviour, kept for parity, and made cheap by
caching the count for 300 s (`cached_exercise_count` in `app/cache/redis.py`).

### 3.6 Legacy `.php` aliases — one file, zero duplicated logic

`app/api/legacy.py` declares seven old URLs. Each one calls the **same service
function** as its modern twin, adds a `Deprecation: true` header and a `Link`
header pointing at the replacement, and logs `legacy_path_used`. When that log
line stops appearing in production, the whole file can be deleted.

| Legacy URL | Modern replacement | Shared service function |
|---|---|---|
| `GET /api/workout.php` | `GET /api/v1/workout` | `workout_service.get_workout` |
| `POST /api/complete-workout.php` | `POST /api/v1/workout/complete` | `workout_service.complete_workout` |
| `POST /save-progress.php` | `POST /api/v1/workout/logs` | `workout_service.save_log` |
| `POST /api/validate_affiliate.php` | `POST /api/v1/affiliate/validate` | `affiliate_service.validate_code` |
| `POST /Payment/create_order.php` | `POST /api/v1/payments/order` | `payment_service.create_order` |
| `POST /Payment/verify_payment.php` | `POST /api/v1/payments/verify` | `payment_service.verify_payment` |
| `POST /start-your-journey.php` | `POST /api/v1/intake` | `intake_service.build_intake_email` |

Legacy routes are hidden from the API docs page (`include_in_schema=False`).

### 3.7 Ops probes — `app/main.py`

| Endpoint | Checks | Why the difference matters |
|---|---|---|
| `GET /health` | Nothing external | *Liveness.* If this touched the database, a 10-second DB hiccup would make the orchestrator kill every healthy pod and turn it into a full outage. |
| `GET /health/ready` | PostgreSQL + Redis | *Readiness.* A pod that cannot serve is pulled from the load balancer without being restarted. Redis down = `degraded`, still ready — the app works without cache. |

---

## 4. Complete endpoint index

`Auth` column: **public** = anyone, **token** = client access token, **JWT** = admin login.

### `/api/v1` endpoints (18)

| # | Method & path | Declared in | Auth | Rate limit | Service it calls | Tables touched |
|---|---|---|---|---|---|---|
| 1 | `POST /intake` | `app/api/v1/intake.py` | public | 5 / 10 min | `intake_service` | — (sends email) |
| 2 | `POST /affiliate/validate` | `app/api/v1/affiliate.py` | public | 30 / min | `affiliate_service` | `affiliate_codes` |
| 3 | `GET /admin/affiliates` | `app/api/v1/affiliate.py` | JWT | — | `affiliate_service` | `affiliate_codes`, `enrollments` |
| 4 | `POST /payments/order` | `app/api/v1/payments.py` | public | 20 / min | `payment_service` | `affiliate_codes` |
| 5 | `POST /payments/verify` | `app/api/v1/payments.py` | public | 20 / min | `payment_service` | `enrollments` |
| 6 | `GET /workout` | `app/api/v1/workout.py` | public | — | `workout_service` | `workout_plans`, `workout_days`, `workout_exercises` |
| 7 | `POST /workout/complete` | `app/api/v1/workout.py` | public | 120 / min | `workout_service` | `workout_logs` |
| 8 | `POST /workout/logs` | `app/api/v1/workout.py` | public | 120 / min | `workout_service` | `workout_logs` |
| 9 | `GET /portal/my-plan` | `app/api/v1/portal.py` | token | — | `portal_service` | `clients`, `workout_*`, `diet_plans` |
| 10 | `GET /portal/progress` | `app/api/v1/portal.py` | token | — | `portal_service` | `clients`, `workout_logs`, `client_progress` |
| 11 | `POST /admin/login` | `app/api/v1/admin.py` | public | 5 / 5 min | `core/security.py` | — |
| 12 | `GET /admin/clients` | `app/api/v1/admin.py` | JWT | — | `client_service` | `clients` |
| 13 | `POST /admin/clients` | `app/api/v1/admin.py` | JWT | — | `client_service` | `clients` |
| 14 | `GET /admin/clients/{client_id}` | `app/api/v1/admin.py` | JWT | — | `client_service` | `clients` |
| 15 | `POST /admin/plans` | `app/api/v1/admin.py` | JWT | — | `plan_service` | `workout_plans/days/exercises` |
| 16 | `POST /admin/plans/import` | `app/api/v1/admin.py` | JWT | — | `plan_service` | `workout_plans/days/exercises` |
| 17 | `POST /admin/diet` | `app/api/v1/admin.py` | JWT | — | `diet_service` | `clients`, `diet_plans` |
| 18 | `POST /admin/progress` | `app/api/v1/admin.py` | JWT | — | `client_service` | `clients`, `client_progress` |

> `/admin/affiliates` (#3) lives in `affiliate.py`, not `admin.py` — it is grouped by
> subject matter, not by URL prefix. That is the one place where file and URL disagree.

### Legacy aliases (7) and ops probes (2)

All seven legacy routes are in `app/api/legacy.py`; both probes are in `app/main.py`.
See the mapping table in section 3.6.

---

## 5. Shared files — what each one is for

```
app/
├── main.py ................ builds the app, mounts routers, health probes
├── middleware.py .......... request id + security headers (every request)
│
├── api/
│   ├── deps.py ............ ★ SHARED GUARDS: DbSession, BodyParams,
│   │                          require_admin (JWT), require_client (token)
│   ├── openapi_ext.py ..... documentation-only helper for the /docs page
│   ├── legacy.py .......... the 7 deprecated .php routes
│   └── v1/{workout,portal,payments,affiliate,intake,admin}.py
│
├── services/ .............. business rules, one file per subject
├── repositories/ .......... all SQL, one file per table group
│
├── core/
│   ├── compat.py .......... ★ PHP-behaviour helpers (php_intval, php_trim, …)
│   ├── config.py .......... ★ every setting and legacy feature flag
│   ├── security.py ........ JWT + admin password + Razorpay signature
│   ├── exceptions.py ...... AppError types → HTTP status codes
│   ├── responses.py ....... fast JSON serialiser
│   └── logging.py ......... structured logs carrying the request id
│
├── cache/
│   ├── redis.py ........... ★ cache get/set, key builders, invalidation
│   └── rate_limit.py ...... ★ the RateLimiter dependency + the 5 budgets
│
├── db/
│   ├── session.py ......... async engine + get_db()  (used by EVERY db route)
│   └── models/ ............ SQLAlchemy tables
│
├── integrations/
│   ├── razorpay_client.py . outbound HTTPS to Razorpay
│   └── mailer.py .......... outbound SMTP
│
└── schemas/admin.py ....... strict request models for admin routes
```

### The four files almost everything depends on

| File | Used by | What breaks if you change it carelessly |
|---|---|---|
| `app/api/deps.py` | 20 of 25 routes | Auth and body parsing for the whole API. Note `BodyParams` accepts **both** form-encoded and JSON, and strips `[]` from repeated keys like `goals[]`. |
| `app/core/compat.py` | 7 of 8 services | Number/string parsing that must match old PHP exactly. Changing `php_intval` changes what `?client_id=abc` returns. |
| `app/core/config.py` | everywhere | Holds the `LEGACY_*` flags that switch preserved bugs on and off (see section 8). |
| `app/cache/redis.py` | workout, portal, admin writes, rate limiting | Cache reads/writes **and** invalidation. Every cache operation fails soft: a Redis outage degrades to a database read, it never returns an error. |

---

## 6. Shared-file matrix

Which shared files each endpoint group touches. `●` = direct use.

| Shared file | Intake | Affiliate | Payments | Workout | Portal | Admin | Legacy |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `api/deps.py` — `BodyParams` | ● | ● | ● | ● | | | ● |
| `api/deps.py` — `DbSession` | | ● | ● | ● | ● | ● | ● |
| `api/deps.py` — `require_admin` (JWT) | | ● | | | | ● | |
| `api/deps.py` — `require_client` (token) | | | | | ● | | |
| `cache/rate_limit.py` | ● | ● | ● | ● | | ● | ● |
| `cache/redis.py` — read/write | | | | ● | ● | | ● |
| `cache/redis.py` — invalidate | | | | | | ● | |
| `core/compat.py` | ● | ● | ● | ● | ● | ● | ● |
| `core/config.py` | ● | | ● | ● | ● | ● | ● |
| `core/security.py` | | | ● | | | ● | |
| `core/exceptions.py` | ● | | ● | ● | ● | ● | ● |
| `repositories/client_repo.py` | | | | | ● | ● | |
| `repositories/workout_repo.py` | | | | ● | ● | ● | ● |
| `repositories/affiliate_repo.py` | | ● | ● | | | | ● |
| `repositories/enrollment_repo.py` | | | ● | | | | ● |
| `repositories/diet_repo.py` | | | | | ● | ● | |
| `integrations/razorpay_client.py` | | | ● | | | | ● |
| `integrations/mailer.py` | ● | | | | | | ● |
| `schemas/admin.py` | | | | | | ● | |

Reading the matrix: `core/compat.py` is ticked in every column — that is the file
you cannot change without regression-testing everything. `schemas/admin.py` is
ticked once — safe to change in isolation.

---

## 7. How endpoints hand data to each other

No endpoint calls another over HTTP. They connect through **shared data**. These
five hand-offs are the real workflow of the system.

### 7.1 Coupon code: affiliate → payments

```
POST /affiliate/validate  ──► browser remembers the code ──► POST /payments/order
        reads affiliate_codes                                    reads affiliate_codes
        (ignores expiry)                                         (enforces expiry)
```

Passed: the coupon string. The field name differs by route — `code` on validate,
`coupon` on order, `coupon_code` on verify. Same value, three names; a real
migration wart worth knowing before you debug a "coupon not applied" report.

### 7.2 Order id: payments/order → payments/verify

```
POST /payments/order ──► { order_id, amount } ──► Razorpay widget ──► POST /payments/verify
                                                   + payment_id
                                                   + signature
```

Passed: `order_id`, plus the `payment_id` and `signature` added by Razorpay.
`verify` recomputes an HMAC over `order_id|payment_id` in `core/security.py`.
Without that check a hand-written POST could create a free `Paid` enrollment —
which is exactly what the old PHP allowed.

### 7.3 Access token: admin/clients → the whole portal

```
POST /admin/clients ──► access_token "GR_ALP_009001" (stored in clients.access_token)
                             │
                             ├──► emailed to the client as a portal link
                             ├──► GET /portal/my-plan?token=…
                             ├──► GET /portal/progress?token=…
                             └──► POST /admin/diet  (identifies the client BY TOKEN,
                                                     not by id — that is what the
                                                     coach types into the admin UI)
```

Generated once by `client_service.build_access_token()` and never regenerated.

### 7.4 Plan JSON: admin/plans/import → workout & portal

```
POST /admin/plans/import          the raw workout_json string is stored VERBATIM
     │                            (never re-serialised — key order would change)
     ├─ writes workout_plans + workout_days + workout_exercises  (one transaction)
     ├─ clears  grind:v1:workout:<client_id>
     ├─ clears  grind:v1:portal:<access_token>
     └─ clears  grind:v1:exercise_count      ← affects EVERY client's percentage
                       │
                       ▼
GET /workout   and   GET /portal/my-plan   now rebuild from the database
```

This is the clearest example of endpoints connected by cache keys rather than
calls. Delete the `invalidate_client_caches()` line and the coach publishes a plan
that the client cannot see for another 60 seconds.

### 7.5 User email: workout/complete → portal/progress

```
POST /workout/complete  { user_email, day_id, exercise_id }
        └─ inserts a row into workout_logs
                  │
                  ▼
GET /portal/progress   counts DISTINCT completed exercises for that same email
                       (looked up from the token's client record)
```

The join key is the **email address**, not the client id — the log table stores
`user_email`. If a client's email is edited in `/admin/clients`, their historical
logs stop counting. Worth knowing before anyone "fixes" an email typo.

### Full lifecycle, end to end

```
 visitor fills the form
    POST /intake ──────────────► staff email
                                     │ (offline: staff replies, agrees a plan)
                                     ▼
 visitor checks out
    POST /affiliate/validate ──► coupon ok?
    POST /payments/order ──────► Razorpay order  ──► customer pays
    POST /payments/verify ─────► enrollments row (Paid)
                                     │
                                     ▼
 coach opens the admin panel
    POST /admin/login ─────────► JWT
    POST /admin/clients ───────► client row  +  access_token  ★
    POST /admin/plans/import ──► workout plan v2  +  cache cleared
    POST /admin/diet ──────────► diet plan (found by access_token)
    POST /admin/progress ──────► weekly measurements
                                     │
                                     ▼
 client opens their link
    GET  /portal/my-plan?token=★ ──► plan + diet + % complete   (cached 60s)
    POST /workout/complete ────────► one workout_logs row per tick
    GET  /portal/progress?token=★ ─► live %, measurement chart  (never cached)
```

---

## 8. Things that will surprise you

These are all intentional, and all documented in the code they live in. This
backend replaces a PHP system and must return byte-identical answers, so several
old bugs are preserved behind flags in `app/core/config.py`.

| Behaviour | Where | Flag |
|---|---|---|
| `GET /workout` with no `client_id` returns client 1; with `?client_id=abc` returns client 0 — never a validation error | `workout.py` + `compat.php_intval` | `LEGACY_DEFAULT_CLIENT_ID` |
| `/affiliate/validate` ignores coupon expiry; `/payments/order` enforces it | `affiliate_service.py` | `LEGACY_AFFILIATE_SKIP_EXPIRY` |
| A 100% coupon creates a zero-amount order (the zero check tests the *original* price, after the discount is applied) | `payment_service.py` | `LEGACY_ZERO_PRICE_CHECKS_ORIGINAL` |
| Progress % divides by *all* exercises in the database, not the client's own | `portal_service.py` | `LEGACY_GLOBAL_PROGRESS_DENOMINATOR` |
| Prices are trusted from the browser; a mismatch is logged, not corrected | `payment_service.py` | `PAYMENTS_RECOMPUTE_PRICE` |
| Razorpay signature verification (the old system skipped it entirely) | `core/security.py` | `RAZORPAY_VERIFY_SIGNATURE` (on) |
| Admin routes had no auth at all in the old system | `api/deps.py` | `LEGACY_OPEN_ADMIN` (off) |
| `POST /workout/complete` twice inserts two rows — no de-duplication, no foreign-key check | `workout_service.py` | — |
| `POST /admin/plans` leaves two active plans; the newest wins by `ORDER BY id DESC` | `plan_service.py` | — |

Failure handling worth internalising:

- **Redis down** → cache reads return `None` (falls back to the database) and rate
  limiting **fails open**. A cache incident must never become a revenue incident.
- **Database down** → `/health/ready` reports `error` and the pod leaves the load
  balancer; `/health` still passes, so the pod is not killed.
- **Razorpay down** → `ExternalServiceError` → HTTP 502 with a generic message.
  Razorpay's own error text is logged but never returned; it can contain account
  detail.
- **SMTP down** → the intake response was already sent, so the failure is only
  logged. The log is the record.

---

## 9. Where to make a change

| You want to… | Edit this |
|---|---|
| Add a new endpoint | a router in `app/api/v1/`, then register it in `app/api/v1/__init__.py` |
| Change a business rule (pricing, validation) | the matching `app/services/*.py` |
| Change a query or add a column read | the matching `app/repositories/*.py` + `app/db/models/` |
| Change who may call a route | `app/api/deps.py` |
| Change a rate limit | the budget constants at the bottom of `app/cache/rate_limit.py` |
| Change what is cached, or for how long | `app/cache/redis.py` + `CACHE_TTL_*` in `app/core/config.py` |
| Turn a preserved legacy bug off | the `LEGACY_*` flag in `app/core/config.py` |
| Retire a `.php` alias | delete its route in `app/api/legacy.py` once `legacy_path_used` stops appearing in the logs |

**Adding an endpoint — the checklist this architecture implies:**

1. Router function in `app/api/v1/<module>.py`; keep it thin.
2. Pick its guards: `DbSession`, `BodyParams` (public/loose) or a Pydantic schema
   (admin/strict), `AdminUser` or `CurrentClient` for auth, and a rate limiter if
   it costs money, sends mail, or can be enumerated.
3. Business rules go in a service; SQL goes in a repository.
4. If it **writes** something a client reads, call `invalidate_client_caches()`.
5. If it **reads** something expensive and rarely changing, cache it with a key
   builder in `app/cache/redis.py` — never an ad-hoc string.
