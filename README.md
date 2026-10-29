# Hook, Line & Sender

A Python webhook relay that accepts producer events, persists delivery work,
sends signed HTTP requests in a separate worker, and records each attempt. The
local demo receiver succeeds immediately by default. An optional failure
simulation lets you see the retry and history flow end to end.

## What does that mean in plain English?

A webhook is a message one app sends another when something happens. For
example, a website's content editor might send "a page was published" so another
app can update its cached pages.

The receiving app might be temporarily offline. This project acts like a
delivery service between the two apps:

1. It accepts a message using a secret API key.
2. It saves the message in a database before confirming acceptance.
3. A separate worker tries to deliver it to the configured receiver.
4. If the receiver has a temporary problem, the worker tries again, up to five
   attempts total.
5. You can inspect the delivery status and the result of each attempt.

Outgoing messages include a cryptographic signature so the receiver can check
that they came from this service and were not changed along the way.

For now, this is a backend project: there is no dashboard or CMS connection.
You interact with it through an interactive API page and a terminal demo.

This is a portfolio and learning project, not a production-readiness or
performance claim.

**Start here:** [first-time setup](#local-setup), then [run the demo](#run-the-demo).
The architecture and signature sections below explain the implementation in
more detail; you do not need to understand them to run it.

## Architecture

```text
Producer ──authenticated POST──► FastAPI ──transaction──► PostgreSQL
                                    ▲                         │
                                    │ reads                   │ pending rows
                                    │                         ▼
                                    └── Periodic dispatcher ─► Redis/Celery
                                                                    │
                                      signed HTTP POST ◄── Worker ◄──┘
                                           │
                                           ▼
                                    Demo receiver
```

PostgreSQL is authoritative for events, delivery state, retry scheduling, and
attempt history. Redis transports tasks; it is not the delivery-history store.
The API commits an event and its one delivery before returning HTTP 202. A
separate dispatcher scans PostgreSQL and can enqueue the same ID more than
once; workers claim rows atomically and ignore active or terminal deliveries.

The interactive API page at [localhost:8000/docs](http://localhost:8000/docs)
includes a plain-English introduction beneath the title and instructions for
submitting an event and checking its delivery history. The modern explorer has
a light/dark toggle, follows your system theme initially, and remembers your
theme choice in your browser. API keys are not persisted across reloads.
Swagger UI assets are loaded from jsDelivr at the pinned version 5.34.0;
loading the interactive explorer requires internet access. External schema
validation is disabled; API requests go to this local service.

## Technology choices

| Component | Selected stable version |
|---|---:|
| Python | 3.12.16 |
| FastAPI | 0.162.0 |
| Pydantic | 2.14.1 |
| SQLAlchemy | 2.1.6 |
| Alembic | 1.20.1 |
| Celery | 5.6.3 |
| Redis Python client | 6.4.0 |
| HTTPX | 0.28.1 |
| psycopg | 3.3.6 |
| pytest | 9.2.0 |
| Ruff | 0.17.3 |
| uv | 0.13.6 |

Dependency versions are exact-pinned in `pyproject.toml` and resolved in
`uv.lock`. Celery's current Redis extra (through Kombu) requires
`redis-py >=4.5.2,<6.5`; therefore the newest Redis client is not necessarily a
compatible broker client. The tested choice is 6.4.0. SQLAlchemy 2.1 requires
Python 3.11 or newer, so this project uses Python 3.12.

The version choices were checked against the official [FastAPI tutorial](https://fastapi.tiangolo.com/tutorial/),
[Pydantic documentation](https://docs.pydantic.dev/latest/),
[SQLAlchemy 2.1 documentation](https://docs.sqlalchemy.org/en/21/),
[Celery stable documentation](https://docs.celeryq.dev/en/stable/), and
[uv project documentation](https://docs.astral.sh/uv/concepts/projects/sync/),
as well as current package metadata on 2026-10-28. Compose uses versioned,
non-`latest` images: Python 3.12.16, PostgreSQL 17.11, and Redis 7.4.12.

## A quick Python orientation

Type hints such as `delivery_id: str` make expected values easier to understand
and check; Pydantic uses model annotations to validate incoming JSON at runtime.
SQLAlchemy sessions are used with Python's `with` context manager to manage
database resources. A `SessionLocal.begin()` block commits on success and rolls
back on errors; a plain session context closes the session but still requires
an explicit commit. A Celery task is a registered unit of background work. This
project uses an explicit `Task` subclass so the editor recognizes task methods
such as `apply_async`; the dispatcher queues delivery IDs for a separate worker.

## Local setup

Run all commands below in VS Code's terminal from the project folder (the folder
containing this README). You do not need a separate terminal for each service.

### 1. Check the tools

- **Docker Desktop** runs the application's services in isolated containers.
  Install it from [Docker's website](https://www.docker.com/products/docker-desktop/)
  and open it. Wait until it reports that its engine is running.
- **uv** installs Python and the project's Python packages. Follow its
  [official installation instructions](https://docs.astral.sh/uv/getting-started/installation/)
  if you do not already have it. uv can download the required Python version;
  you do not need to install Python separately for this project.
- **OpenSSL** generates random local passwords; it is available on most macOS
  development setups.

Check the commands are available:

```sh
uv --version
docker compose version
openssl version
```

If `uv` is not found after installation, open a new terminal and follow the
installer's PATH instructions. PATH is the list of folders your terminal
searches for commands.

### 2. Install Python dependencies (the equivalent of `npm install`)

From the repository root, run:

```sh
uv sync --locked --all-groups
```

`uv` installs the required Python version if necessary, creates `.venv`, and
installs the locked application and development dependencies there. Think of
`pyproject.toml` as `package.json`, `uv.lock` as `package-lock.json`, and `.venv`
as the project's isolated Python interpreter plus installed packages (roughly
the role of `node_modules`). Do not commit `.venv`.

In VS Code, open the Command Palette, choose **Python: Select Interpreter**,
and select `.venv/bin/python` in this repository. This lets Pylance find the
same packages your commands use. Run project commands with `uv run --locked`;
you do not need to activate the virtual environment manually.

Docker installs its own dependencies inside each application image. The local
`.venv` is for your editor, tests, and the demo script; the API, worker,
dispatcher, receiver, PostgreSQL, and Redis run in Compose.

### 3. Configure local secrets

The `.env` file holds settings for your machine. It is ignored by Git because
it contains secrets. `.env.example` is the safe template, not the file you put
real passwords in.

**If your `.env` already contains generated secrets, skip this step. Do not copy
the template over a configured file.** For a new checkout:

```sh
cp -n .env.example .env
openssl rand -hex 32
openssl rand -hex 32
openssl rand -hex 32
```

Open `.env` in VS Code and paste the three different generated values:

| Setting | What to put there | What it is for |
|---|---|---|
| `API_KEY` | First generated value | Lets you submit events and read delivery history |
| `DEMO_SIGNING_SECRET` | Second generated value | Lets the demo receiver verify outgoing messages |
| `POSTGRES_PASSWORD` | Third generated value | Password for the local database |

Replace the password between `hookline:` and `@db` in **both** `DATABASE_URL`
and `TEST_DATABASE_URL` with the same third value. Keep the `db` hostname and the
database names unchanged. The generated hexadecimal values do not need special
URL escaping.

Never share `.env` or commit it to Git. Changing `POSTGRES_PASSWORD` after the
database has already been created does not automatically change the password
stored in that database; see troubleshooting below.

### 4. Start the application

```sh
docker compose up --build -d
```

This builds the application and starts six services: API, worker, dispatcher,
demo receiver, PostgreSQL, and Redis. `-d` means they stay running without
occupying your terminal. The first build may take a few minutes.

The API and receiver bind only to localhost (your own machine). PostgreSQL and
Redis are accessible only within the Compose network. Database data stays in a
persistent Docker volume when services are stopped.

### 5. Create or update the database tables

```sh
docker compose run --rm api alembic -c backend/migrations/alembic.ini upgrade head
```

A **migration** is a versioned database change, such as creating tables.
This command applies any changes that have not been applied yet; it is safe to
run again. Wait for it to finish successfully before submitting events.

### 6. Check that the services are running

```sh
docker compose ps
curl -f http://localhost:8000/health/ready
```

The readiness request should return HTTP 200 with `"status":"ready"`. If it
fails immediately after startup, wait a few seconds and try again. If it keeps
failing, see [logs and troubleshooting](#logs-and-troubleshooting).

Now run the demonstration below.

## Run the demo

With the stack running and migrated, execute:

```sh
uv run --locked --env-file .env python -m scripts.run_demo
```

The command resets only the local receiver's in-memory demo state, submits a
sample `content.published` event, polls until completion (45 seconds by
default), prints each attempt, and exits nonzero if it does not succeed. Use
`--timeout 60` to change the bound. By default the receiver succeeds on its
first attempt; ordinary delivery is not forced to fail.

Expected output includes:

```text
Attempt 1: succeeded (HTTP 200)
Demo completed successfully.
```

To deliberately test retry behavior, opt in:

```sh
uv run --locked --env-file .env python -m scripts.run_demo --simulate-retries
```

Only this mode makes the demo receiver return HTTP 503 twice, then HTTP 200.
Both modes reset the receiver's shared in-memory state before submitting a
fresh event; avoid running them simultaneously. The script uses the API key
from `.env`; there is nothing to paste into the terminal command.

### Try the API in your browser

1. Open [http://localhost:8000/docs](http://localhost:8000/docs).
   Use the **Dark mode / Light mode** button in the header to change the theme.
2. Click **Authorize**. Copy only the `API_KEY` value from your local `.env`,
   paste it into the value field (without `API_KEY=` or a `Bearer` prefix), and
   confirm.
3. Expand **GET /api/v1/deliveries**, click **Try it out**, then **Execute**.
   The response shows the deliveries created by your demo runs.
4. Copy a delivery's `id` and use **GET /api/v1/deliveries/{delivery_id}** to
   see its status and attempt history.

### Start it again on another day

Your dependencies, configuration, and database data are already saved:

```sh
docker compose up -d
uv run --locked --env-file .env python -m scripts.run_demo
```

After pulling code changes, run `uv sync --locked --all-groups`, start with
`docker compose up --build -d`, and re-run the migration command above.

## API example

```sh
curl -i http://localhost:8000/api/v1/events \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "payload-demo",
    "source_event_id": "publish-page-about-v1",
    "type": "content.published",
    "destination_id": "demo-receiver",
    "data": {
      "collection": "pages",
      "document_id": "page_123",
      "slug": "about"
    }
  }'
```

The response is HTTP 202 with `event_id`, `delivery_id`, and `status_url`. Query
the status URL with the same bearer token to see chronological attempts. Other
business routes (`GET /api/v1/events/{event_id}` and
`GET /api/v1/deliveries?status=pending&limit=50&offset=0`) also require the API
key. `/health/live` checks only process liveness; `/health/ready` returns 503 if
PostgreSQL or Redis is unavailable.

The incoming request body limit is 256 KiB and is enforced even when
`Content-Length` is absent. Reuse the same `source_event_id` when retrying an
ambiguous submission failure. The identity `(source, source_event_id,
destination_id)` is unique. Equivalent validated content returns the original
IDs; conflicting content returns HTTP 409.

## Outgoing request and signature contract

The trusted operator configuration defines the only destination,
`demo-receiver`. An event can choose its ID but cannot supply a URL.

The exact compact, deterministic UTF-8 request body is persisted when the
delivery is created and reused for every attempt. Each POST includes:

- `X-Hookline-Event-Id`: stable internal event UUID.
- `X-Hookline-Delivery-Id`: stable delivery UUID and receiver idempotency key.
- `X-Hookline-Timestamp`: Unix time in seconds, freshly generated per attempt.
- `X-Hookline-Signature`: `sha256=` followed by a lowercase hexadecimal HMAC.

To verify, compute HMAC-SHA256 with the destination secret over the ASCII
timestamp, a literal period, and the exact raw HTTP body bytes:

```text
HMAC_SHA256(secret, timestamp + "." + exact_raw_request_body)
```

Compare the expected and supplied signatures in constant time. Reject
timestamps more than five minutes from current time. The included receiver
implements these checks. Fresh timestamps prevent stale requests but do not
deduplicate them; use the stable delivery ID to make receiver side effects
idempotent.

The local Compose configuration explicitly permits HTTP only to its
`receiver` service hostname. This is a local-development exception, not a
recommendation for public destinations. Before supporting user-supplied URLs,
the service needs SSRF protections, address validation at connection time,
redirect restrictions, and outbound network controls. A URL syntax check alone
does not solve SSRF.

## Delivery semantics and retry policy

Statuses are `pending`, `in_progress`, `retry_scheduled`, `succeeded`, and
`failed`. A worker takes a PostgreSQL row lock, creates an attempt, and writes a
lease before making an HTTP request outside the transaction. The lease lasts
the configured overall request budget plus a recovery margin, rounded up to
whole seconds. `REQUEST_TIMEOUT_SECONDS` defaults to 3 seconds and covers the
whole HTTP operation, not a separate allowance for each connection/read/write
phase. Its deadline starts when the delivery is claimed, so time spent committing
the claim reduces the remaining network budget. It uses a monotonic countdown
that is not affected by wall-clock corrections. The worker cancels an overdue
HTTP operation and checks for an expired budget before opening the HTTP client.
Cancellation is not a guarantee against operating-system stalls or process
pauses; lease recovery and receiver-side deduplication are still necessary.

The worker checks status and headers without consuming the response body. A
receiver that sends HTTP 200 and then stalls its body therefore does not cause
a retry. Redirects and environment-configured HTTP proxies remain disabled.

After an expired lease, a later task records the stranded attempt as
`worker_lost` and retries if the five-attempt limit has not been reached.
Claim tokens prevent a late result from an old worker from overwriting the
newer attempt. These safeguards cannot eliminate duplicates after a process
pause, crash, lost response, or receiver-side processing; receiver idempotency
is still required.

- Any 2xx is success.
- Network errors, timeouts, 408, 429, and 5xx are retryable.
- Other 4xx responses and all 3xx responses are terminal.
- There are five total attempts, including the initial request.
- Retry delay is exponential (default 1, 2, 4, 8 seconds), with up to 250 ms
  jitter and a configurable 30-second cap. Valid numeric or HTTP-date
  `Retry-After` values are honored but capped to the same maximum. Invalid,
  non-finite, or excessively long values are ignored.
- Reaching the attempt limit records `failed`.

PostgreSQL `next_attempt_at` is the authoritative retry schedule; Celery does
not maintain a competing retry timer. If Redis is unavailable, the dispatcher
logs the publish failure and leaves the committed row eligible for another
scan. For a local resilience check, stop Redis, submit an event, start Redis
again, and observe the delivery progress:

```sh
docker compose stop redis
# Submit an authenticated event while Redis is stopped.
docker compose start redis
```

The API can still accept events while Redis is down, although readiness reports
503 and delivery waits for the dispatcher to reconnect. If a receiver processes
a request but its response is lost, the sender cannot know whether the side
effect happened and may retry. Delivery is not exactly-once and eventual
success is not guaranteed. Ordering is not guaranteed.

### Dependency failures and configuration

`DEPENDENCY_TIMEOUT_SECONDS` defaults to 3 (allowed range: 2-30). It limits
PostgreSQL connection/pool waits, individual statements and lock waits, and
Redis connection/read waits. These are per-operation limits, not a promise that
an entire multi-step readiness check finishes within exactly three seconds.
The dispatcher does not perform inline publish retries: a broker failure stops
the current batch, logs a sanitized error, and leaves pending work for the next
scan. Celery still reconnects its worker when the broker returns.

Readiness checks require the delivery table to exist as well as a responding
database and Redis. Liveness remains independent of those dependencies.

Invalid timing, retry, and size settings fail at startup rather than allowing a
busy loop or an unusable request limit. Retry base cannot exceed its cap.
Settings may reduce, but not raise, the milestone's 256 KiB body limit,
five-attempt maximum, or five-minute signature tolerance.

## Tests and checks

The quick tests use mocked outgoing requests and do not require Docker:

```sh
uv run --locked pytest -m "not postgres" -q
```

`pytest` is the test runner. A passing result means the tested behavior matched
expectations; `deselected` means the PostgreSQL-only tests were deliberately
excluded, not that they failed.

Ruff checks code style and formatting (similar to ESLint and Prettier):

```sh
uv run --locked ruff format --check .
uv run --locked ruff check .
```

PostgreSQL uniqueness and locking tests use an actual PostgreSQL service. Run
them against the separate `hookline_test` database initialized by Compose:

```sh
docker compose --profile test run --build --rm integration-tests
```

Each PostgreSQL integration test creates a uniquely named temporary schema and
removes that schema afterward, rather than clearing existing application tables.
Still use a dedicated test database, not production credentials.
The tests cover simultaneous submissions and claims, duplicate dispatcher
messages, expired-worker recovery, stale results, and lost-response duplicates.
PostgreSQL's one-time initialization creates
`hookline_test` for new volumes; if your existing volume predates this setup,
create it once with `docker compose exec db createdb -U hookline hookline_test`.
CI runs formatting, lint, unit tests, migrations, and PostgreSQL integration
tests separately.

## Logs and troubleshooting

```sh
docker compose ps
docker compose logs -f api worker dispatcher receiver
docker compose logs -f db redis
```

Logs are JSON and include available event IDs, delivery IDs, attempt numbers,
statuses, and outcomes. Secrets and authorization headers are not logged.

- **`No module named uv`:** use `uv sync` and `uv run`, not `python3 -m uv`.
  The activated project Python does not need uv installed inside it. If the
  `uv` command is missing, fix PATH using the installation instructions above.
- **Cannot connect to Docker:** open Docker Desktop and wait for its engine to
  start, then retry `docker compose up --build -d`.
- **Port already in use:** another app may be using port 8000 or 8081. Stop
  that app before starting this stack.
- **Database password authentication failed:** check that both database URLs
  match `POSTGRES_PASSWORD`. If you changed the password after creating the
  persistent database, its stored password is still the old one. Restore the
  previous working configuration or deliberately update the PostgreSQL role's
  password. Do not delete the volume unless you intend to erase local data.
- If services fail to start, check `docker compose ps` and the relevant service
  logs. Ensure the local secret values were replaced in `.env`.
- If the API starts but readiness is 503, check PostgreSQL and Redis health.
- If an event remains pending, check dispatcher and Redis logs; accepted work
  remains persisted in PostgreSQL.
- If migrations fail, confirm `DATABASE_URL` uses service hostname `db` from
  inside Compose and that its password matches `POSTGRES_PASSWORD`.
- The demo receiver keeps test counters in memory; restarting it resets those
  counters. The demo command also resets them before a run.

Stop containers without deleting data:

```sh
docker compose down
```

To explicitly erase the local PostgreSQL volume and all persisted local events
and deliveries:

```sh
docker compose down --volumes
```

This destructive reset is never part of normal startup. To reset only the
receiver's in-memory demonstration state, run the demo command or send
`POST /demo/reset` to `http://localhost:8081`. This also restores immediate
success. `POST /demo/reset?failures=2` enables the optional two-failure mode
until the next reset or receiver restart. These controls are development-only.

## Known limitations and roadmap

Milestone 1 uses one API key and one trusted configured destination, stores
receiver demo state in memory, and has no dashboard, public destination
registration, multitenancy, replay, fan-out, ordering, or cloud deployment.
It is a local engineering demo and makes no production-readiness or performance
claim.

- **Milestone 2:** Payload CMS producer and Next.js App Router receiver
  examples; verify signatures before content revalidation; use stable producer
  event IDs; handle draft/published changes, CMS transaction boundaries, and
  relay-acceptance failures explicitly; verify against the installed Payload
  and Next.js versions.
- **Milestone 3:** Next.js delivery-history dashboard with filters, attempt
  details, and controlled manual replay. Keep the relay's administrative API
  key server-side and out of browser code.
- **Milestone 4:** Craft CMS integration example plus the additional operations
  and security work needed before considering public hosting.

Future arbitrary destinations require SSRF protections, address validation at
connection time, redirect restrictions, and outbound network controls.
