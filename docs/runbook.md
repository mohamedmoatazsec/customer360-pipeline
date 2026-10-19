# Run and recover

## Local commands

Install the project from the README, then run:

```sh
python -m customer360 run --fixture tests/fixtures/sample.json --output data/demo
python -m customer360 run --output data/live --page-size 50
python -m pytest
python -m ruff check .
```

Use one writer per output directory. Keep the destination on a local filesystem. All reads should resolve `current.json`; do not scan for the latest run folder. The command exits zero only after publication, and nonzero on extraction, contract, SQL or export failure.

## Before consuming a snapshot

1. Read `current.json` and resolve `run_dir` relative to the output root.
2. Confirm `status` is `passed` and inspect `completed_at` for the consumer's freshness requirement. There is no automatic freshness SLA.
3. Inspect `quality.json`: all 28 checks should have zero failures.
4. Use the Parquet files for typed analysis; CSV is a convenient inspection export.

## Failure handling

| Failure | What to inspect | Recovery |
|---|---|---|
| HTTP 429 or 5xx | Safe error and provider availability | Three bounded attempts use 1- then 2-second backoff; rerun later if exhausted. No unbounded retry loop. |
| HTTP 4xx other than 429 | URL/provider contract | Fix the source configuration. These responses are not retried. |
| Network or timeout | Connection and availability | Each request has a 15-second timeout; bounded retries apply. |
| Pagination total changes | Source changed mid-extract | Rerun a fresh full snapshot. Never combine a partial extract with the previous run. |
| Missing/invalid fields or duplicate IDs | `failures/<run_id>.json` | Inspect the provider contract or the local fixture. Do not disable validation to get a green run. |
| SQL quality failure | Named checks and failure counts in the failure report | Correct the source/test input or fix a demonstrated transformation bug, then rerun tests and the pipeline. |
| Unexpected internal failure | Permissions, free disk space, installed runtime | Fix the underlying issue. The report intentionally avoids serializing arbitrary exceptions or source values. |

The last successful pointer is retained when a run fails. No automated rollback is necessary for an unpublished failure. To recover an older successful run, verify its `run.json` and artifacts before deliberately republishing its pointer. This repository does not include a rollback command.

## CI and live verification

`CI` runs lint and offline tests on Python 3.11, 3.12 and 3.13 for pushes and pull requests. It does not rely on a third-party API being available. Tests cover pagination, transient errors, malformed payloads, SQL aggregates, contact minimization, repeatability, CLI failures and preservation of the last good pointer.

`Live API smoke test` is a separate **manual** GitHub Actions workflow. In the repository's Actions tab, select it and choose **Run workflow**. It performs real read-only requests, uploads only the run summary, quality report and category totals, and fails if the quality gate fails. It needs no secrets. Artifact retention is 14 days. There is no scheduled job or running cloud service.

External API outages should fail the live check while leaving the offline CI suite usable. Investigate which layer failed; do not confuse provider availability with transformation correctness.

## Retention and privacy

Local `data/` is ignored by Git. Do not commit the full warehouse, bronze snapshots or customer-level exports. Although the source is synthetic, keeping the published evidence aggregate-only demonstrates data minimization. No credentials are required; never add API keys or `.env` contents to Git.

Completed run folders and failure reports accumulate locally. Delete only explicitly selected, obsolete runs after ensuring no consumer uses them; preserve the run referenced by `current.json`. This project has no automatic retention or background process.
