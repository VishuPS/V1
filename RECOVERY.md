# Missing-product recovery

Final valid lookup misses are deduplicated by canonical GTIN into a durable
database queue when `RECOVERY_ENABLED=true`. The existing API response stays
unchanged. Queue capture shares the analytics transaction and its best-effort
failure handling; an analytics/database failure can lose a capture, but never
turn a lookup into an error. Each request counts once per unique missing GTIN.

## Activate

1. Apply `python -m alembic upgrade head` to the target database.
2. Set `RECOVERY_ENABLED=true` on the API and worker.
3. Configure the existing provider enablement, credentials and persistence flags.
4. Set `RECOVERY_WORKER_ENABLED=true` on the existing single-worker API service. The API supervises a serial recovery subprocess processing up to 20 jobs every five minutes. Alternatively run `python -m app.recovery --limit 20` separately.
5. Inspect `python -m app.recovery --status --limit 100`.

The CLI exits after a bounded batch unless `--watch` is specified. The supervised subprocess uses `--watch`; it restarts after failure and stops with the API. Keep one API worker and replica when using this mode. Provider throttles remain process-local; provider backoff state is shared through PostgreSQL.
Disable recovery to stop capture and worker execution without removing jobs.

## Behavior

Jobs with more requesting API calls are prioritized, oldest first on ties.
Atomic conditional updates prevent simultaneous active claims. A 15-minute lease
allows recovery after worker termination; network work runs outside the queue
transaction. Jobs stop after five claims, including crashed attempts. Unsuccessful
attempts wait 1, 2, 4, then 8 days, preserving provider negative-cache cooldowns.
Unresolved jobs stay available for inspection and are not automatically reopened.

The worker checks local data first, then reuses provider adapters with
`persistent_only=True`. Google Books is excluded. All adapters respect their existing persistence rules. Production providers include Open Facts, UPCItemDB, EAN-DB and Open Icecat; temporary candidates are never saved. Transient candidates cannot stop
the search for a later eligible source. Matching canonical GTIN and a nonempty
name are required before importing through the existing conservative merge and
provenance path. Existing source rules continue to determine storage eligibility.

Status output contains demand counts, attempts, next attempt, recovered provider
and failure detail. This initial implementation has no new web admin view,
distinct-customer prioritization, dollar-budget accounting, customer webhook,
manual-review workflow, or product cache to invalidate. The job limit bounds
work per invocation; it is not a monetary spending cap. Provider errors currently
use the same conservative retry schedule as misses.

Tests cover API capture, canonical deduplication, leases, retry exhaustion,
existing local products and skipping transient candidates before durable import.
Production PostgreSQL concurrency/load validation remains a deployment check.
