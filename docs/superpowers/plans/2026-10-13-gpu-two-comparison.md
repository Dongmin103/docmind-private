# GPU inference concurrency2 comparison

User approved comparison then explicitly limited new measurement to concurrency2 only; reuse prior324/concurrency1 baseline22.930355s. Base3d510dd. Keep document3/parser2/CPU4, request batch32, model FP32/eager, internal batches8/2/1, embedding request2, decrypt1 and the exact14-document885-vector manifest. No permanent deployment.

1. Build a benchmark-only adapter sharing one model across two CUDA streams, bounding requests2, protecting shared tokenizer mutation, synchronizing streams before reuse, and recording content-free timing/OOM events. Test concurrency/isolation/failure release before execution.
2. Recreate dedicated pipeline resources in a new ignored experiment directory. Read-only original corpus; separate SQL/index. Temporarily drain development ingest/watcher and stop original GPU service to avoid duplicate residency. Preserve all original files/config and task states; restore in finally.
3. Synthetic readiness/correctness check, one separate corpus warmup, exactly ONE measured concurrency2 run. Do not run concurrency1 again. Record elapsed/GPU/VRAM, event overlap, errors/OOM, vector counts, output dimensions and finite values. Compare to previous baseline; disclose non-contemporaneous/cold allocator differences and any interference.
4. Verify source fingerprints, cleanup and task/API/GTE restoration; save sanitized report/JSON. Fresh review of benchmark adapter and fidelity; no merge/push/deployment.

Ruling: reuse existing isolated worktree and native scripts; keep this durable plan/ledger rather than shell-only skill helpers. Wrong harness assumptions could invalidate comparison, so recheck binding/caps and corpus.
Ruling: concurrency2 uses distinct CUDA streams; merely changing a CPU semaphore would not prove separate GPU execution queues. CUDA streams permit but do not guarantee simultaneous kernels; no profiler proof claim.

Review focus: stream ownership and error synchronization, tokenizer state, no model copy, same batch/input/output contract, isolated DB endpoint binding, restore original service, baseline comparability.

Progress: current deployed service is text-identical to repository source (byte hash differs only due encoding/newlines). Existing baseline can be used as user requested. No new concurrency1 run planned.

User addition: separate request waiting, tokenization and GPU inference timing. Added request gate/tokenizer timers and CUDA events around model/normalization. Enqueue and copy/wait wall intervals are reported separately as overlapping observations, not summed components.

Completed: adapter tests observed missing-module RED then12/12 GREEN including existing service tests. Synthetic concurrent requests passed shape/order/finite/norm checks. One corpus warmup then ONE measured GPU2 run passed14/885; no GPU1 rerun. Measured26.987995s versus historical22.930355s (+17.7%); GPU49.9percent, VRAM6291MiB, noOOM. Request wait0.667s/tokenization0.261s/CUDA stream interval sum21.978s. API embedding stage sum decreased25.183→23.764s while parse_http increased11.541→23.964s; GPU concurrency is not established as the cause of slower end-to-end time.

Preparation findings: scheduler stop left original GPU Python child alive briefly; exact executable/PID checked and processes fully terminated before candidate start. Dedicated Redis credentials corrected before any corpus run. Restoration after preparation failures temporarily restarted service; duplicate original launch children were identified and removed before candidate GPU load. Final measured run used only candidate model; these were preparation attempts, not extra measured runs.

Fresh read-only reviewer: no Critical/Important. Minor deferred: event-log-write exception can leak a request permit; add synchronization-failure poison test. Ruling: keep prototype-only refinements deferred because normal measured run had52/52 successful requests and candidate is removed; if reused under log-storage failure, fail/restart the experiment. Cost is benchmark availability, not adoption of an unverified production setting.
Review interpretations: CUDA stream intervals include contention and launch gaps; no proof of simultaneous kernels, no adding CUDA and copy-wait durations. Instrumentation overhead exists only in candidate and comparison is non-contemporaneous. Model cache/thread safety was not statically proven, but885 outputs match existing live GTE vectors at minimum cosine0.9999999999991. Remote/model-general workloads and permanent deployment remain outside scope.

Final verification: original14source fingerprints unchanged;885 vectors matched; all temporary host/API/parser files0; isolated containers/DB removed; candidate listener gone; original GTE/API HTTP200; GTE/ingest/watcher scheduled tasksRunning. Focused suite12/12 rerun passed. Results: docs/GPU-INFERENCE-2-RESULTS-2026-10-13.md and docs/measurements/gpu-inference-2-2026-10-13.json. Retain original inference1. Worktree preserved; no merge/push/permanent deployment.
