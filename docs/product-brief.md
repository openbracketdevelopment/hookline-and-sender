# Hook, Line & Sender: Product Brief

## Purpose

Hook, Line & Sender is a locally runnable webhook relay for accepting producer
events, delivering them to configured destinations, and preserving an auditable
history of delivery attempts. The first milestone demonstrates durable
acceptance, independent background delivery, signed requests, and bounded
retries.

This is a portfolio and learning project, not a production-readiness or
performance claim. Its first destination is a local receiver that intentionally
succeeds immediately by default, with an opt-in mode that fails twice and then
succeeds to demonstrate retries.

## Milestone 1 scope

- Authenticate API requests with one operator-configured bearer API key.
- Accept validated events for one operator-configured destination per event.
- Persist events, delivery state, and immutable attempt history in PostgreSQL.
- Dispatch durable pending deliveries from PostgreSQL to a separate Celery
  worker through Redis.
- Sign each outgoing request with HMAC-SHA256.
- Expose authenticated event, delivery, and attempt-history reads.
- Demonstrate two retryable receiver failures followed by successful processing.
- Include migrations, tests, Compose-based local setup, and GitHub Actions checks.

Not included: a dashboard, CMS integrations, public destination management,
accounts, teams, billing, multitenancy, Kubernetes, cloud infrastructure,
Terraform, AI features, event fan-out, guaranteed ordering, or manual replay.

## Event contract

`POST /api/v1/events` accepts an object with `source`, `source_event_id`, `type`,
`destination_id`, and JSON-object `data`. The service creates its own event and
delivery IDs and UTC accepted timestamp. Request bodies are limited to 256 KiB,
including requests without a `Content-Length` header.

The idempotency identity is `(source, source_event_id, destination_id)`.
Producers must reuse `source_event_id` when retrying an ambiguous submission.
Equivalent validated content returns the original IDs without another delivery;
conflicting content with the same identity returns HTTP 409.

## Delivery rules

PostgreSQL is authoritative for pending work, statuses, retry times, leases, and
attempt history. Redis transports Celery tasks but is not the delivery-history
store. Event acceptance commits before returning HTTP 202 and does not require
Redis to be reachable. A periodic dispatcher finds due records and can enqueue
the same ID more than once; atomic claims and terminal-state checks make
duplicate tasks harmless.

Each destination is an operator-configured URL. The event body is serialized
once and persisted byte-for-byte before the first attempt. Each attempt sends
that body in a POST with a fresh timestamp and HMAC signature.

Any 2xx response succeeds. Network errors, timeouts, HTTP 408, HTTP 429, and
5xx responses are retryable. Other 4xx responses and redirects are terminal.
There are five total attempts, including the initial request. Retry delays
increase exponentially, include small jitter, and are capped; `Retry-After` is
honored when valid and capped to the same maximum. Reaching the attempt limit
records a terminal failure.

Delivery is at-least-once in the presence of ambiguous network/process failures,
not exactly-once. A receiver can process a request whose response is lost, so
receivers must use the stable delivery ID to make side effects idempotent.
Event ordering and guaranteed eventual success are not promised.

## Acceptance criteria

- A new developer can follow the README to start and migrate the local stack.
- Authenticated events persist even when Redis is unavailable.
- A separate worker delivers the event and keeps chronological attempt records.
- The default demo succeeds immediately. With `--simulate-retries`, the receiver
  fails twice, then succeeds; all attempts are visible by API.
- Repeating an equivalent submission does not create another delivery.
- Tests cover authentication, validation, size limits, conflict handling,
  delivery policy, signatures, retry limits, duplicate tasks, lease recovery,
  and Redis-independent acceptance.

## Known limitations and later milestones

The demo has one configured destination, one API key, in-memory receiver state,
and no guaranteed ordering, fan-out, replay UI, or public-hosting deployment.
Local HTTP is allowed only for the explicitly enabled demo destination.

- **Milestone 2:** Payload CMS producer and Next.js App Router receiver examples,
  with signed verification, stable IDs, draft/published and transaction-boundary
  handling, and explicit relay-acceptance failure behavior.
- **Milestone 3:** Next.js delivery-history dashboard with filters, attempt
  details, and controlled manual replay; administrative credentials stay
  server-side.
- **Milestone 4:** Craft CMS integration example and further operational and
  security work before any public hosting.

Future user-configured destinations require SSRF protections, address validation
at connection time, redirect restrictions, and outbound network controls. A
URL syntax check alone does not prevent SSRF.
