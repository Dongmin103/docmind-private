# Execution ledger — plan: docs/superpowers/plans/2026-10-13-parser-cpu-324.md

Base:092c59c, branch perf/parser-cpu-324. User explicitly switched planning to implementation of the five-condition matrix ending at324.

Ruling: native worktree creation returned Not a git repository because chat cwd is outside the repo; created an ignored repository-local Git worktree from the verified concurrency commit. Existing active worktree left untouched.
Ruling: use this durable progress file and native Windows tools rather than shell-dependent skill helper scripts; the approved plan was previously chat-only and has now been made concrete here.
Pre-flight interfaces: server capacity and URL limiter must both become2; API admission and all Windows validators must both accept3; measurement harness must mount all changed paths and use a separate parser instance with matching capacity/quota.

Task1 evidence: warmed A4/A5 parse_http sum7.301/8.022s and embedding14.539/14.372s; B4/B5 parse_http sum20.003/13.266s and embedding17.085/18.320s. Host decrypt sum2.244–2.451s. These are overlapping sums, not wall time. Parser wait not separated in old records; instrumentation needed before causal attribution.
Baseline attempt: Docker CLI rejected Windows-style bind paths; use /mnt/c paths on the Linux daemon.

User revision: ONE measured run per each of112,212,222,224,324, warmups separate. No medians or repeatability claims.
Implementation gates:35Node and138Python tests passed before review. Review Important findings reproduced by real sockets (slow drip body and expired incomplete upload) and fixed;9client/6pool tests passed. Full final suites pending.
Trial diagnostics: initial warmup14/885 valid; run1-112 valid; run1-212 had13 successes and1 HOST_WORKER_ERROR, no ingestion audit for that failed attempt, after31seconds in host delivery. Exclude failed run. Cleanup0, no container OOM. Small HTTP control transport stress40requests passed, maximum26ms; root cause not established.
Ruling: final review is read-only fresh-context reviewer; two important findings fixed. Minor suggestions deferred: cap test could create fourth eligible job; preserve capacity2 parameterization. Existing competing claim test covers cap3.

Final validation: Node39/39 including real LibreOffice conversion cleanup; Python140/140; Windows three-child pool/isolation/decrypt/parent-death test passed; removed-runtime verification passed. The short upload-timeout tests now check post-timeout admission using malformed JSON (400), avoiding unrelated cold child startup in the recovery assertion.

Tasks1–5 complete: final code f27aeb9; one measured run per112/212/222/224/324, warmups separate. Times37.470/26.394/31.985/24.076/22.930seconds; GPU mean34.10/48.50/39.53/53.43/56.85percent. All five14documents/885vectors, cleanup0, noOOM. Final324 reduced baseline38.8percent; 50percent aspiration not reached. Repetitions and repeatability claims explicitly omitted per user instruction.

Final cleanup evidence: original fingerprints14/14 unchanged; dedicated containers/database removed; ingest and source watcher scheduled tasks bothRunning; original API healthz HTTP200. Worktree/branch and ignored private measurement logs preserved; no permanent deployment, merge, or push.

User bottleneck follow-up: final two large embedding intervals HWP9.840s/PDF9.623s overlap at batch offsets12.86–22.79s, occupying77.3percent of summed embedding stage time. Device-wide GPU average89.1percent over that tail (47samples). Their parse_http times1.393/4.115s. Remaining tail is primarily embedding path; tokenization/wait/inference split not measured. No extra benchmark run added.

Final report: docs/CPU-PARSER-324-RESULTS-2026-10-13.md; sanitized evidence: docs/measurements/cpu-parser-324-2026-10-13.json. Important review issues fixed; two minor test refinements deferred as above. Branch remains as-is because the authorized task is implementation and isolated comparison, not integration or deployment.
