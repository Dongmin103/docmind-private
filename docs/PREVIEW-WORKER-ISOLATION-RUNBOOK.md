# Preview worker isolation

2026-10-13 (Asia/Seoul). Implementation and isolated verification record.
Live deployment is **on hold**: the observed cgroup peaks exceed the proposed
90% gate. This does not diagnose database corruption or a current database
outage. cgroup `memory.peak` includes earlier load; the collector never resets
it or masks a container restart as a new baseline.

## Runtime contract

Run `Start-DocMindUEncryptorHostWorker.ps1 -Purpose ingest|preview`. Default is
`ingest`. Each role uses its own worker ID, plaintext root, receipt root, log
and scheduler task. Both configurations use the same `decrypt_control_root`.
The ingest launcher preserves the captured source/format/retry filters; the
preview launcher uses only the previously approved configured sources.

Global `DocMind-uEncryptor2-SingleRun` serializes executable execution and
integrity checks. Artifact delivery and signed cleanup ACKs run after release.
Preview wait is bounded by 30 seconds and lease expiry minus ten seconds.
The signed read-only `/cloud-sync/host-worker/previews/<id>/validate` route
checks authority during waiting and again before launch. It never renews leases.
Cancellation during execution still relies on existing timeout and final
artifact/status validation; it does not instantly interrupt the executable.

The child enters a Windows Job Object atomically at process creation
(`PROC_THREAD_ATTRIBUTE_JOB_LIST`), before its suspended thread is resumed.
`KILL_ON_JOB_CLOSE` covers descendants if the parent dies. `owner.json` records
PID/start time/executable hash/phase, without paths or contents. An incomplete
owner, a present/reused PID, an abandoned mutex or an unconfirmed termination
creates a persistent `blocked.json`. Neither worker clears a block automatically.
An operator must independently prove all matching decrypt processes have
exited before recovering that metadata. Do not delete plaintext roots to clear
the block. Keep receipts until their original signed status ACK is confirmed.

Limits remain 64MiB input, 16MiB derived, 96MiB reservations, two sessions,
128MiB shared tmpfs, 256MiB processor, one ingest worker, one preview worker and
one converter. Polling is one second; heartbeat is thirty seconds. The browser
records `docmind-preview-click-to-first-page` only after PDF `pagerendered`
and the following animation frame. Blob delivery and READY are not render proof.
Other formats do not silently emit that PDF render metric.

## Prepare, review, apply

All private manifests, scheduler XML, original launcher/config copies, code
snapshots, image IDs, watcher states and measurements stay in ignored `.local/`.
Prepare does not change scheduler state, sources, containers or model bindings.
It reads the actual scheduled launcher and deployed worker/common files.
Unsupported launcher forms fail instead of guessing their source scope.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/windows/Set-DocMindPreviewWorkerIsolation.ps1 `
  -Action Prepare -ManifestPath C:/DocMindDev/docmind/.local/preview-worker-isolation/release/manifest.json
```

Missing memory or functional reports produce failed gates, so this manifest
cannot be applied. For a release candidate, supply immutable reviewed
`-MemoryReportPath` and `-FunctionalReportPath`. Acknowledge backup exclusions
for the **new** plaintext roots with `-BackupExclusionAcknowledged` only after
actually configuring them. Prepare preserves enabled/running states, including
disabled watchers. It generates disabled tasks with `MultipleInstancesPolicy`
`IgnoreNew` and role-specific launchers with bounded API readiness backoff.

Before a user-authorized Apply, deploy the verified web/API changes through
the current release process. Preserve current image IDs/environment/mounts;
do not rerun the old October 1 Compose file. This worker tool does not replace
API images or change database/schema/index/model data. API health and presence
of the signed validation route are required before starting new workers.

Apply checks exact prepared/source hashes, reparse boundaries, BitLocker when
required, acknowledged new backup exclusions, shared gate ownership and
quiescence. Existing plaintext work blocks conversion. Original receipts are
replayed using the original worker ID/version/fence and original status paths;
any failed ACK blocks conversion. Scheduler changes begin only after these
checks. A second check under the global gate catches newly claimed work.
Partial registration failure stops both new tasks before restoring the old
XML and enabled/running states. If safe recovery cannot be proved, phase becomes
`recovery-required`; do not start an old worker beside a new worker.

```powershell
# Only after explicit live-application authorization and all release gates.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/windows/Set-DocMindPreviewWorkerIsolation.ps1 `
  -Action Apply -ManifestPath C:/DocMindDev/docmind/.local/preview-worker-isolation/release/manifest.json

powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/windows/Set-DocMindPreviewWorkerIsolation.ps1 `
  -Action Rollback -ManifestPath C:/DocMindDev/docmind/.local/preview-worker-isolation/release/manifest.json
```

Rollback requires quiescence, disables/stops both new tasks, confirms no role
singleton or legacy process remains, then restores only the original mixed
task and its captured state. New roots/receipts are retained; no database rollback,
source deletion, original relocation or reindexing occurs.

## Verification and release gates

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/windows/Test-DocMindPreviewWorkerIsolation.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/windows/Test-DocMindDecryptGate.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/windows/Test-DocMindWorkerThroughput.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/windows/Test-DocMindUEncryptorHostWorker.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/windows/Test-DocMindPreviewWorkerDeployment.ps1
```

The gate tests use synthetic executables and a private mutex namespace. They
exercise real Windows parent/child overlap, sender waiting after release,
serialized decryption, timeout/tree termination, parent kill, reused PID,
incomplete ownership, persistent block and double release. Deployment tests
use a synthetic filesystem and mocked scheduler: scope/filter preservation,
disabled-state preservation, exact hashes, blocked preflight, partial failure,
idempotent Apply and exclusive Rollback.

Read-only measurement (default 900 seconds; use at least 1200 seconds for a
five-minute baseline plus fifteen-minute overlap):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/windows/Measure-DocMindPreviewIsolation.ps1 `
  -DurationSeconds 1200 -IntervalSeconds 1 `
  -OutputDirectory C:/DocMindDev/docmind/.local/preview-worker-isolation/measurements/candidate
python app/scripts/preview_worker_isolation_probe.py evaluate `
  --windows .local/preview-worker-isolation/measurements/candidate/windows.jsonl `
  --linux .local/preview-worker-isolation/measurements/candidate/wsl.jsonl `
  --output .local/preview-worker-isolation/measurements/candidate/result.json
```

Sampler output distinguishes Windows availability/commit/worker private and
working set from WSL availability/swap/PSI and per-container cgroup
current/peak/events. Shared tmpfs is counted by hashed backing-store identity
once. Missing measurements are failures, never zero. Container changes and OOM
counter/peak resets fail the run. Read access to kernel diagnostics is needed;
missing kernel evidence prevents a passing release gate.

The browser probe requires an isolated, prepared synthetic fixture page and
Playwright Chromium. Use the production preview component, a <=1MiB/<=5-page
synthetic PDF, an already started browser/API, twenty trials and a signed
synthetic ingest responder held for 540 seconds. Supply private storage state
only through `--storage-state`; no credentials are written to the report.
Run `preview_worker_isolation_probe.py browser --url <fixture-page>` with an
ignored `--output` path. Compare p95 <=5000ms against the same fixture/load
before isolation. Large PDF/Office/HWP, two tabs, capacity, disconnect/cancel,
termination failure, worker restart and Rollback remain separate scenarios.

## Recorded evidence and limits

- Windows role/gate/throughput/cleanup/deployment suites: PASS.
- Isolated Linux Python 3.13 preview service + HTTP: 53 PASS.
- Frontend document-preview Jest: 29 PASS across four suites.
- Memory evaluator unit tests: PASS (missing data, units, shared tmpfs,
  OOM/reset/restart, independent Windows/WSL thresholds, swap and cgroup peaks).
- Global TypeScript check: existing baseline 236 errors, changed tree 235,
  no new diagnostics. Whole Vite build fails in both trees because the installed
  dependency tree cannot resolve `highlight.js/lib/languages/sql_more`.
- Five-minute memory observation recorded privately under
  `.local/preview-worker-isolation/measurements/baseline-20261013/`; it includes
  existing application activity and verification, so it is not an idle proof.
- Memory gate fails on existing cgroup peaks. No limits, BGE service, model
  bindings, production tasks, containers, source files or database data were
  changed. Latest GTE deployment documents confirm GTE binding, but do not alone
  prove BGE receives no requests from any remaining path; BGE use remains
  unconfirmed and no stop/removal is authorized here.
- Live Apply, live representative preview/ingest/cleanup ACK, twenty browser
  first-page trials, full format matrix and a passing fifteen-minute candidate
  load test have **not** been performed. No before/after browser p95 or extra
  worker idle-memory guarantee is claimed.

Original private recovery material lives beside the prepared manifest in
`before/`; new reviewed artifacts live in `after/`. The actual live task remains
the captured `DocMind Host Worker` until a separately authorized release.
