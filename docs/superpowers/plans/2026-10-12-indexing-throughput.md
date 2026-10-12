# Indexing Throughput Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Eliminate avoidable ingestion and initial scan delays while retaining source consistency and recovery.

**Architecture:** Keep the native SQLite queue, signed per-source outbox, and separate scan process. Add idempotent scan discovery, prioritize due rechecks, and process bounded source visits without sleeping while progress is possible. Continue normal polling when idle or delivery fails.

**Tech Stack:** Windows PowerShell 5.1, C#, winsqlite3, Python synthetic signed HTTP endpoint.

**Spec:** `docs/superpowers/specs/2026-10-12-indexing-throughput-design.md`

## Global Constraints

- Retain ordinary modification debounce of 120 seconds and second observation spacing of 10 seconds.
- Source visits: at most 32 due entries and a 1-second budget, checked between entries.
- Preserve signed ACK validation, immutable outbox, fencing, generation-safe ACK and single decrypt execution.
- No production restart, deployment, real-source writes, or real-document reindex in this run.
- Preserve complete scan inventories and deletion authority only after successful enumeration.

## Review Focus

- A scan racing with a pending move/delete must preserve that event and its generation.
- Continuous dirty events must not starve due second observations.
- Fenced or unacknowledged delivery must stop the source visit and retain retry sleep.
- Failed document jobs may continue immediately only after confirmed cleanup and status ACK.
- Unsupported suffixes may belong to registered legacy documents; do not silently discard them without mapping evidence.

### Task 1: Scan discovery and due priority

**Files:** Modify `tools/windows/DocMindSyncQueue.cs`, `tools/windows/Invoke-DocMindSourceReconciliation.ps1`; test `tools/windows/Test-DocMindSyncQueue.ps1`.

**Interfaces:** Produce `long EnqueueScan(string source, string path, long now)`; existing pending rows return their generation unchanged, absent rows become immediately due initial registrations. `Due(source, now, limit)` prioritizes stable_count > 0 among already-due entries. No new wire kind.

- [x] Add failing assertions: scan candidate immediately due; repeated scan preserves fingerprint/10-second due; existing upsert retains 120 seconds; move/delete/outbox survive scan; obsolete ACK cannot delete scan's newer event; restart preserves initial timing; due recheck wins over older first observations.
- [x] Run `powershell -NoProfile -ExecutionPolicy Bypass -File tools/windows/Test-DocMindSyncQueue.ps1`; expected new regression failure.
- [x] Implement transaction-safe EnqueueScan and due priority; use EnqueueScan during streaming enumeration.
- [x] Run queue and collector tests; expected PASS.
- [x] Commit queue/scan change and tests.

### Task 2: Bounded continuous source processing

**Files:** Modify `tools/windows/Watch-DocMindEncryptedSources.ps1`; create `tools/windows/Test-DocMindSyncThroughput.ps1`.

**Interfaces:** Produce `Process-SourceChanges -Id`, returning boolean progress after one dirty batch plus at most 32 due operations within 1 second. Process-Due returns whether an entry was progressed or delayed. Due selection rechecks each iteration, rather than retaining stale snapshots.

- [x] Add function-extraction tests using the real SQLite queue and stubbed signed delivery: 40 ready entries exceed one visit but drain over repeated visits; 10-second rechecks stay enforced; due second observation proceeds alongside dirty traffic; missing file only delays itself; pending outbox and fenced session stop processing; time budget stops starting new entries.
- [x] Run new test; expected missing source processing function failure.
- [x] Implement bounded source visits, boolean progress and progress-driven outer loop; preserve one immutable outbox per source.
- [x] Run new test and existing incremental signed HTTP test; expected PASS.
- [x] Commit watcher change and tests.

### Task 3: Continuous worker claims

**Files:** Modify `tools/windows/Start-DocMindUEncryptorHostWorker.ps1`; create `tools/windows/Test-DocMindWorkerThroughput.ps1`; update outdated watcher contract assertion in `tools/windows/Test-DocMindUEncryptorHostWorker.ps1`.

**Interfaces:** Job function reports confirmed status/cleanup via a referenced boolean to distinguish safely finalized document failure from transport/cleanup uncertainty. Worker loop sleeps only when no finalized job was processed or cycle infrastructure failed.

- [x] Extract and execute the actual loop with fake claims/jobs and captured sleep calls: consecutive successful jobs have no sleep; safe failure continues; empty queue, claim error and unconfirmed failure sleep; Once makes one pass; 3-preview fairness and targeted ingestion claim preserved.
- [x] Run new test; expected sleep between ready jobs failure.
- [x] Implement progress tracking and safe finalization signal; update stale legacy deletion-endpoint assertion to current signed change protocol contract, without weakening deletion checks.
- [x] Run worker throughput and existing host-worker tests; expected PASS.
- [x] Commit worker changes and tests.

### Task 4: Streaming integration, measurements and review

**Files:** Extend `tools/windows/test_sync_stub.py`, create `tools/windows/Test-DocMindScanThroughput.ps1`; update spec status and create `docs/INDEXING-THROUGHPUT-2026-10-12.md`.

**Interfaces:** Synthetic endpoint accepts signed scan lifecycle/candidates, tracks sequence per source, and records timestamps. Test introduces a slow scan reader after initial entries so the watcher can finish an earlier file while scanning continues.

- [x] Add failing integration proving initial files finish two signed observations before scan completion; failed enumeration cannot send completed/candidate events.
- [x] Run regression against baseline scripts; expected no second observation within test deadline due to 120-second wait.
- [x] Run against implemented scripts; expected PASS, observed stable interval >=10 seconds, full inventory maintained.
- [x] Compare baseline/updated watcher on equal aged-upsert fixtures to isolate scheduler improvement; document source-stage measurements separately from full indexing.
- [x] Run all targeted PowerShell tests, Python syntax check and git diff check.
- [x] Obtain fresh independent code review; address substantive findings and rerun affected verification.
- [x] Commit validation record and leave feature branch ready for deployment review.
