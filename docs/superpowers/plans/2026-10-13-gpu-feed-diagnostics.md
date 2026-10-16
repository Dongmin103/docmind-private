# GPU input supply diagnostics implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Separate the observed 8.6-second overlapping delay, then verify a supported improvement with one measured corpus run.

**Architecture:** Opt-in benchmark instrumentation records executor queue/execution/CPU time, event-loop lag and content-free call locations, plus DB/index call durations. Use the existing isolated benchmark harness and original 14-document corpus. Keep GPU inference 1 and length-aware batches unchanged to isolate the next change.

**Tech Stack:** Python standard library, Quart/asyncio, Windows host worker, Docker, existing GTE service.

**Spec:** User-approved next steps and `docs/GPU-BUCKET85-RESULTS-2026-10-13.md`.

## Constraints and review focus

- No document content, SQL text, arguments, results, credentials or exception messages in diagnostics.
- No operating-service code/config deployment. Drain and restore original scheduled services around the isolated trial.
- Same 14 documents/885 vectors; unchanged original fingerprints and completed plaintext cleanup required.
- One diagnostic warmup and one measured run; no new baseline run. Do not invent a fix if the delay does not reproduce.
- Instrumentation must preserve exceptions and results, distinguish queue from CPU time, detect loop blockage and remain bounded.

## Tasks

- [x] Add `tools/local_embedding/pipeline_probe.py` and focused unit tests for queued work, exceptions, content exclusion and event-loop blockage.
- [x] Copy the private benchmark harness into `.local/gpu-feed-20261013`, attach the probe only to its API, run diagnostic warmup and inspect the evidence.
- [x] If evidence supports a local fix, add a failing regression test and minimal implementation. Otherwise document the remaining uncertainty; do not guess.
- [x] Run the one measured trial, compare stages, GPU use and correctness, restore services and remove isolated resources.
- [x] Record sanitized results and independent review, run focused checks and commit the reviewable work.

## Execution decisions

- Observed synchronous authentication DB transaction on API loop; moved verification to worker with lazy DB opening and guaranteed close. Original 8.6-second stall not reproduced, so do not identify authentication as its cause.
- Review caught eager connection acquisition before HMAC validation; corrected and re-reviewed. API/auth tests 30 and embedding/probe tests 20 passed.
- API restarts required two additional preparation runs. One had reset client concurrency1 and was excluded; final warmup and the only measured run used client2. No extra measured baseline or candidate run.
- Probe expanded from main-loop monitoring to per-document asyncio.run loops. Diagnostic cross-phase block counts are not comparable.
- Measured document span 20.60s versus28.85s previously, but controller observation41.87s with19.81s post-completion delay. Cause unresolved. Read-only repository verification overlapped orchestration, adding a possible resource confound.
- Did not add a preparation queue or more CPU workers: measured executor queue waits were small. Evidence and limitations are in `docs/GPU-FEED-RESULTS-2026-10-13.md`.
