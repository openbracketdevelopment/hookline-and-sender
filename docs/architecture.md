# Hook, Line & Sender: Architecture

## Components

```text
Producer
  │ authenticated POST
  ▼
FastAPI ───────────────► PostgreSQL
                           │ durable delivery rows
                           ▼
                     Periodic dispatcher ──► Redis/Celery ──► Worker
                                                                  │ signed POST
                                                                  ▼
                                                          Demo receiver

FastAPI reads delivery and attempt history from PostgreSQL.
```

PostgreSQL is the source of truth for event acceptance and delivery state.
Redis is only a task transport. The API commits event and delivery records
before returning 202; an independent periodic dispatcher retries publishing
eligible IDs after temporary Redis outages.

The code uses synchronous SQLAlchemy sessions and a synchronous PostgreSQL
driver. FastAPI performs short database operations; it does not hold a
transaction while making HTTP requests. Celery runs a separate synchronous
worker. The dispatcher is a separate process in Compose.

Only the outgoing HTTP operation uses an asynchronous HTTPX client, run within
the synchronous worker using `asyncio.run`. Python's `asyncio.timeout` cancels
the whole operation at a single deadline. This avoids treating HTTPX's separate
per-operation inactivity limits as an overall deadline; database access stays
synchronous.

## Data model

- **Event:** UUID, producer source and stable source event ID, event type,
  validated JSON data, and UTC accepted time.
- **Delivery:** UUID, event and destination references, exact persisted outbound
  JSON bytes, status, attempt count, authoritative next-attempt time, lease
  expiration and claim token, dispatch throttle timestamp, and audit timestamps.
- **DeliveryAttempt:** UUID, delivery and attempt number, start/completion time,
  HTTP status, duration, outcome, and bounded sanitized error summary.

The database enforces uniqueness of `(source, source_event_id, destination_id)`.
The event row and one delivery row are created in the same transaction.

## State and dispatch

Supported delivery states are `pending`, `in_progress`, `retry_scheduled`,
`succeeded`, and `failed`. The dispatcher queries due pending/retry rows and
expired in-progress leases. It publishes only delivery IDs; PostgreSQL retains
the retry schedule. Failed publish attempts are logged and leave work eligible
for a later scan.

A worker claims a due delivery under a row lock using `SELECT ... FOR UPDATE`
and creates its attempt record before committing. An active lease or terminal
status makes duplicate/stale tasks no-ops. The worker performs the network call
outside the transaction, then atomically records its outcome and advances the
delivery state. The clock is read after acquiring the lock. A lease expires
after the configured overall request budget plus a recovery margin, rounded up.
The monotonic request deadline starts at claim time; committing the claim
consumes part of that budget. An already expired budget prevents opening the
HTTP client. Cancellation cannot guarantee immediate cleanup of operating-system
stalls or process pauses. The HTTP
client consumes no response body: only response status and headers are needed.
The next claim marks a stranded attempt as `worker_lost` and either makes it due
again or fails it if the five-attempt limit was reached. Claim tokens fence
late completions from a previous worker out of a replacement attempt.

PostgreSQL connect/pool, statement, and lock waits are limited by
`DEPENDENCY_TIMEOUT_SECONDS` (default 3). Redis connection/read waits use the
same setting. These are individual operation limits, not an overall readiness
deadline. Publish retries are disabled inside Celery's publisher; the dispatcher
logs a broker failure, stops that batch, and tries persisted work on its next
scan. Concurrent dispatcher scans may still publish duplicate IDs; atomic
worker claims remain the final coordination mechanism.

A receiver can have performed a side effect before a process crash or lost
response. Retrying may therefore repeat delivery; receivers should deduplicate
by the stable delivery ID. Delivery is not exactly-once, strictly ordered, or
guaranteed eventually to succeed.

## API and authentication

`/docs` serves a custom light/dark API explorer around Swagger UI 5.34.0.
Its shell, styles, and theme logic are local assets; the pinned Swagger bundle
and stylesheet come from jsDelivr. Theme preference is stored in the browser,
but authorization is not persisted. External schema validation is disabled.

`/activity` serves a local activity dashboard using the existing authenticated
delivery list and detail endpoints. It supports status filters, pagination, and
attempt history, refreshing every ten seconds while visible. Its API key is
held only in JavaScript memory and cleared on disconnect or reload. The
dashboard loads no CDN assets and displays no event payloads.

Committed acceptance, attempt-start, attempt-result, and expired-lease events
are emitted to the `hookline.activity` logger after database commit. With
`ACTIVITY_LOG_DIR` set, this logger also writes JSON lines to `webhooks.jsonl`.
Compose mounts the host's `logs/` directory into the API, worker, and dispatcher.
Rotation retains five 5 MiB backups; an OS file lock serializes writes and
rotation across processes on macOS/Linux. Only allowlisted context is recorded,
not bodies, credentials, signatures, or raw exception messages. File errors are
reported on stderr. These files are best-effort operational logs: a crash
between database commit and logging can omit a line, and rotation removes old
entries. PostgreSQL remains authoritative for dashboard history.

- `GET /health/live`: process liveness.
- `GET /health/ready`: checks the delivery table is queryable and Redis responds.
- `POST /api/v1/events`: bearer-authenticated ingestion.
- `GET /api/v1/events/{event_id}`: authenticated event and delivery details.
- `GET /api/v1/deliveries`: authenticated, paginated/filterable list.
- `GET /api/v1/deliveries/{delivery_id}`: authenticated state and ordered
  attempts.

The one incoming API key is configured by the operator. It is separate from the
demo destination signing secret. Business endpoints all require bearer auth.
An ASGI body limiter enforces a 256 KiB limit even without `Content-Length`.
Startup rejects invalid or non-finite timing and size configuration. The
milestone's request-size, attempt-count, and signature-tolerance maxima cannot
be raised through environment settings.

The event request's validated fields are normalized for idempotency comparison;
whitespace and JSON object key order do not affect equivalence. The outbound
body is serialized once using deterministic compact JSON and stored as text so
each attempt signs and sends the same bytes.

## Outgoing signature

Each POST includes `X-Hookline-Timestamp` (Unix seconds), `X-Hookline-Signature`
(`sha256=<lowercase hex digest>`), `X-Hookline-Event-Id`, and
`X-Hookline-Delivery-Id`.

The HMAC-SHA256 input bytes are the ASCII timestamp, a literal `.`, and the
exact UTF-8 bytes sent as the request body:

```text
HMAC_SHA256(secret, timestamp + "." + exact_raw_request_body)
```

The receiver compares signatures in constant time and accepts timestamps only
within a five-minute tolerance. The timestamp is freshly generated for each
attempt. Timestamp freshness does not prevent duplicates; receivers must also
deduplicate by delivery ID.

## Destination trust boundary

Milestone 1 loads `demo-receiver` from operator environment configuration.
Events select only this configured identifier and never provide a URL. Local
HTTP is accepted only with the explicit development flag and local demo
hostname. Database and Redis ports are not published by Compose; API and demo
receiver ports bind to localhost.

Before any user-supplied destination feature, add SSRF protections, resolve and
validate addresses at connection time, prevent redirects, and apply outbound
network controls. Syntax validation alone is insufficient.

## Technology choices and compatibility

The initial implementation targets Python 3.12 and stable package releases
verified against the projects' official documentation and package metadata on
2026-10-28. The selected dependency ranges are recorded in `pyproject.toml` and
the resolved transitive dependencies in the committed `uv.lock`.

The current stable Celery Redis extra resolves through Kombu and requires
`redis-py >=4.5.2,<6.5` (excluding two specific releases); the project therefore
pins a compatible Redis client rather than the newest incompatible major
release. SQLAlchemy 2.1 requires Python 3.11 or newer, which is why the project
targets Python 3.12 rather than the system's older Python runtime.

## Limitations

The demo receiver keeps counters in process memory, so a receiver restart resets
its demo state and restores immediate success. The development-only reset
endpoint can opt into two simulated failures; `scripts.run_demo
--simulate-retries` enables this mode for retry demonstrations. There is one
configured destination and a single operator API
key. There is no CMS integration, account isolation, manual replay,
fan-out, ordering guarantee, cloud deployment, or production-readiness claim.
