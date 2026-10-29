# Hook, Line & Sender: Implementation Plan

This plan describes Milestone 1 only. Later milestones are documented in the
product brief and README; they are not part of this implementation.

## Work sequence

1. Define the product scope, delivery contract, architecture, acceptance
   criteria, and known limitations in the accompanying documents.
2. Pin supported Python dependencies, resolve and commit `uv.lock`, and add
   configuration, structured logging, SQLAlchemy models, and an Alembic
   migration.
3. Implement authenticated event ingestion and authenticated event, delivery,
   attempt-history, liveness, and readiness endpoints.
4. Persist stable outbound request bytes. Add a PostgreSQL-backed periodic
   dispatcher, idempotent Celery task claims, expiring leases, signed HTTP
   requests, and bounded database-scheduled retries.
5. Add the signed local receiver, its reset and state controls, and a bounded
   end-to-end demo command.
6. Add unit and PostgreSQL integration tests, Compose health checks and
   persistent storage, local developer instructions, and GitHub Actions checks.
7. Run formatting, lint, test, migration, and Compose checks available in the
   environment; report any unavailable services or checks explicitly.

## Acceptance checklist

- [ ] Database transaction stores an event and its one configured delivery.
- [ ] Duplicate equivalent submissions reuse IDs; identity conflicts return 409.
- [ ] Request size is bounded with and without `Content-Length`.
- [ ] Ingestion persists successfully without Redis; dispatcher later publishes.
- [ ] Worker claims are atomic, lease recovery is bounded, and duplicate/stale
  task messages do not re-send active or completed deliveries.
- [ ] Each attempt is preserved and uses the same body bytes with a fresh
  timestamp and HMAC signature.
- [ ] Retry classification, `Retry-After`, backoff, jitter, and five-attempt
  limit match the documented policy.
- [ ] Receiver rejects invalid/old signatures, fails twice, succeeds, and
  deduplicates already-processed deliveries.
- [ ] Developer setup, reset, demo, troubleshooting, limitations, and roadmap
  are documented; CI checks formatting, lint, and tests.

## Verification notes

Tests that rely on PostgreSQL uniqueness and row locking must run against a real
PostgreSQL service. HTTP delivery unit tests mock the network; the Compose demo
is the small service integration path. No performance numbers are claimed.
