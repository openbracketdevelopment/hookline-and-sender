# Hook, Line & Sender

A Python service that receives webhooks, saves them, and delivers them to
another application. It retries temporary failures and keeps a record of every
delivery attempt.

A **webhook** is a message one application sends another when something
happens, such as a page being published in a CMS.

```text
Your CMS → Hook, Line & Sender → Your application
```

The relay saves each message before confirming receipt. Outgoing messages are
signed so the receiving application can verify their origin and integrity.

**Current scope:** a local learning and portfolio project with one configured
demo receiver and an activity dashboard. CMS integrations and multiple
destinations are not included yet. It is not production-ready.

## Requirements

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) — runs the
  application and its supporting services. Open it before starting.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — installs
  Python and the project's dependencies.
- **OpenSSL** — generates local secrets; available on most macOS development
  setups.

Run all commands from the project folder in your terminal.

## First-time setup

### 1. Install dependencies

```sh
uv sync --locked --all-groups
```

This is the Python equivalent of `npm install`. It installs the required Python
version and packages in a local `.venv` folder.

For reference:

| Python | Similar role in Node.js |
|---|---|
| `pyproject.toml` | `package.json` |
| `uv.lock` | `package-lock.json` |
| `.venv` | Isolated Python environment and installed packages |
| `uv run` | Runs a command using the project's environment |

In VS Code, open the Command Palette, choose **Python: Select Interpreter**,
and select this project's `.venv/bin/python`. You do not need to activate the
environment manually when using `uv run`.

### 2. Configure your environment

**Skip this step if your `.env` is already configured. Do not overwrite it.**

For a new checkout, copy the template and generate three separate secrets:

```sh
cp -n .env.example .env
openssl rand -hex 32
openssl rand -hex 32
openssl rand -hex 32
```

Open `.env` and use the generated values:

| Setting | Value |
|---|---|
| `API_KEY` | First secret: authorizes access to the API |
| `DEMO_SIGNING_SECRET` | Second secret: signs outgoing messages |
| `POSTGRES_PASSWORD` | Third secret: protects the local database |

Also replace the password between `hookline:` and `@db` in **both**
`DATABASE_URL` and `TEST_DATABASE_URL` with the third secret. Keep the rest of
each URL unchanged.

Keep `.env` private. It is ignored by Git and must not be committed.

### 3. Start the services

```sh
docker compose up --build -d
```

This starts the API, delivery worker, dispatcher, demo receiver, PostgreSQL,
and Redis. The first build may take a few minutes. The `-d` flag keeps them
running without occupying your terminal.

### 4. Set up the database

```sh
docker compose run --rm api alembic -c backend/migrations/alembic.ini upgrade head
```

This applies **migrations**: versioned changes that create or update database
tables. It is safe to run again.

### 5. Check the application

```sh
docker compose ps
curl -f http://localhost:8000/health/ready
```

The readiness check should return `{"status":"ready"}`. If it fails just after
startup, wait a few seconds and retry.

## Run the demo

With the services running, submit a sample event:

```sh
uv run --locked --env-file .env python -m scripts.run_demo
```

The script reads your API key from `.env`, submits the event, and displays the
delivery result. Expected output includes:

```text
Attempt 1: succeeded (HTTP 200)
Demo completed successfully.
```

To test recovery from temporary failures:

```sh
uv run --locked --env-file .env python -m scripts.run_demo --simulate-retries
```

This optional mode makes the demo receiver fail twice before succeeding.
Each demo run resets the receiver's shared in-memory state, so run one demo
at a time. Normal demo runs succeed immediately.

## Explore the API

Open **[localhost:8000/docs](http://localhost:8000/docs)**.

1. Click **Authorize** and paste the `API_KEY` value from `.env`.
   Do not include `API_KEY=` or a `Bearer` prefix.
2. Expand **GET /api/v1/deliveries**.
3. Click **Try it out**, then **Execute** to list deliveries.
4. Copy a delivery's `id` into **GET /api/v1/deliveries/{delivery_id}** to
   inspect its status and attempt history.

To submit your own event, use **POST /api/v1/events** with a body like this:

```json
{
  "source": "cms-demo",
  "source_event_id": "page-about-published-v1",
  "type": "content.published",
  "destination_id": "demo-receiver",
  "data": {
    "slug": "about"
  }
}
```

A successful submission returns HTTP `202` with an event ID, delivery ID, and
status URL. This means the event was **saved for delivery**, not that it has
already reached the receiver.

The explorer supports light and dark themes and remembers your choice.
Authorization is cleared on reload. Its Swagger UI assets load from a CDN,
so the explorer requires internet access.

## View webhook activity

Open **[localhost:8000/activity](http://localhost:8000/activity)**, or select
**Webhook activity** beside the API explorer tab.

1. Paste your `API_KEY` from `.env` and click **Connect**.
2. Filter deliveries by status or browse older pages.
3. Click **View** to see a delivery's attempts, HTTP responses, and errors.

Activity refreshes every 10 seconds while the page is visible. Your key stays
in memory only; reloading or clicking **Disconnect** clears it. The dashboard
shows accepted deliveries from PostgreSQL, including history from before this
dashboard was added. It does not show message payloads or rejected API requests.

### Activity log files

Docker saves new acceptance and delivery activity as JSON lines in
`logs/webhooks.jsonl`. Each line includes a timestamp, event and delivery IDs,
and relevant status, attempt number, HTTP status, duration, and sanitized error.
Payloads, API keys, and signing secrets are excluded.

```sh
tail -f logs/webhooks.jsonl
```

Logs rotate at 5 MiB, retaining five backup files. They survive container
restarts and are ignored by Git. Older database history is not copied into
these files. PostgreSQL remains the source of truth: file logs are a bounded
operational aid, not a complete audit archive. File-write failures are reported
in service logs without discarding committed webhook records.

File logging uses macOS/Linux file locks to coordinate the API and worker
processes. Compose configures it automatically; when running Python directly,
set `ACTIVITY_LOG_DIR` to a private, writable directory to enable it.

## Everyday commands

### Start again

Your configuration and database data are preserved:

```sh
docker compose up -d
```

### Update after pulling code changes

```sh
uv sync --locked --all-groups
docker compose up --build -d
docker compose run --rm api alembic -c backend/migrations/alembic.ini upgrade head
```

### View logs

```sh
docker compose logs -f api worker dispatcher receiver
```

Press **Ctrl+C** to stop following logs. The services keep running.

### Stop

```sh
docker compose down
```

This preserves database data. Adding `--volumes` **permanently deletes local
events and delivery history**; it is not part of normal startup.

## Tests and code checks

Run the quick tests without Docker:

```sh
uv run --locked pytest -m "not postgres" -q
```

`deselected` means the database tests were intentionally skipped, not failed.

Check formatting and code quality:

```sh
uv run --locked ruff format --check .
uv run --locked ruff check .
```

With Docker running and `.env` configured, run the PostgreSQL tests:

```sh
docker compose --profile test run --build --rm integration-tests
```

These use the separate `hookline_test` database and temporary test schemas,
not the application's tables. If an older Docker volume is missing that
database, create it once:

```sh
docker compose exec db createdb -U hookline hookline_test
```

## Troubleshooting

| Problem | What to check |
|---|---|
| `uv` is not found | Restart your terminal and follow uv's installation instructions for PATH. |
| `No module named uv` | Use `uv sync` or `uv run`, not `python3 -m uv`. |
| Docker cannot connect | Open Docker Desktop and wait for its engine to start. |
| Port already in use | Check whether another application uses port 8000 or 8081. |
| Readiness returns `503` | Apply migrations and check PostgreSQL and Redis with `docker compose ps`. |
| A delivery stays pending | Check dispatcher, worker, and Redis logs. Saved events remain in PostgreSQL. |
| Database login fails | Ensure both database URLs match `POSTGRES_PASSWORD` and use hostname `db`. |

Changing `.env` does **not** change an existing database's password. If you
changed it after setup, restore the working configuration or update the
PostgreSQL role's password. Do not delete the database volume as a routine fix.

For database or Redis logs:

```sh
docker compose logs -f db redis
```

## Delivery behavior and limits

- **Durable acceptance:** PostgreSQL stores events before the API confirms
  receipt. Redis queues work; it does not store delivery history.
- **Retries:** up to five total attempts. Network errors, timeouts, HTTP `408`,
  `429`, and `5xx` responses can retry. Any `2xx` succeeds; other responses
  end the delivery. Redirects are not followed.
- **Duplicate submissions:** reuse the same `source`, `source_event_id`, and
  `destination_id` when retrying a submission. Matching content returns the
  original IDs; changed content returns HTTP `409`.
- **Receiver safety:** signed messages may still arrive more than once.
  Receivers must use the stable delivery ID to avoid repeating side effects.
  Exactly-once delivery, ordering, and eventual success are not guaranteed.
- **Current limits:** one API key, one trusted destination, and a maximum
  incoming request size of 256 KiB. Demo receiver state is kept in memory.

The local API and receiver are accessible only from your machine; PostgreSQL
and Redis are internal to Docker. Public hosting and user-supplied destination
URLs require additional security and operational work.

## Technical reference

Built with Python 3.12, FastAPI, PostgreSQL, SQLAlchemy, Celery, Redis, and HTTPX.
Dependencies are pinned in [pyproject.toml](pyproject.toml) and [uv.lock](uv.lock).

- [Architecture](docs/architecture.md) — components, signatures, retry handling,
  configuration, and security boundaries.
- [Product brief](docs/product-brief.md) — project goals and scope.
- [Implementation plan](docs/implementation-plan.md) — milestones and future
  integration work.
