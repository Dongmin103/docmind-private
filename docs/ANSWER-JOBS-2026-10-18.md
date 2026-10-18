# Asynchronous DOCMIND answers

The baseline is the running `docmind-ragflow:folder-sync-20261016` image
(`f5f5dcf033cd1c18b4ef2ea1b8d72ba1c3e04040ff8a37750159706cdbda3a7a`).
All 287 deployed Python files were captured and compared. The deployed public
frontend index matches the reviewed-answer build from `bab0b802`.
The baseline commit also preserves the current folder sync and HWPX parser changes.

`POST /api/v1/docmind/answer/jobs` accepts the existing question, project, scope
and neighbor window, plus a random 32-character hexadecimal `request_id`.
It returns HTTP 202 with an opaque job ID and status. Repeating the same request
ID and payload returns the existing job. An ID collision with another owner or
payload is rejected. `GET /api/v1/docmind/answer/jobs/<id>` returns queued,
running, completed (including the verified result), or failed (including a safe
error code). Both operations require the existing login and tenant boundary.
The previous synchronous answer endpoint is replaced by this contract.

Jobs and the global lease coordinator use two additive tables:
`docmind_answer_job` and `docmind_answer_queue`. Request and result payloads are
encrypted with authenticated AES-GCM using a key derived from the existing
application secret. The existing Redis is a 128 MiB volatile-LRU cache and is
therefore not used as the authoritative answer queue.

Each API process runs a background consumer. A transactional coordinator row
serializes claims across processes, allowing one answer job at a time. Leases
last 30 seconds and renew every 8 seconds. A lost lease cancels local generation;
its token cannot publish a late result. Another process can reclaim an unfinished
job after lease expiry, with at most two processing attempts. The queue holds at
most 16 active jobs. Jobs waiting over 30 minutes fail explicitly when claimed.
Request/result records expire after one hour and are physically removed by the
consumer. Polling does not extend retention.

Generation and review share a 600-second budget, including their bounded repair
attempt. The entire answer operation is capped at 720 seconds. Context limits,
provider/model pinning, source checks, citation validation and
the maximum three model calls remain unchanged. Generation and review allow an
output limit of 2048 or 4096 tokens; the deployed setting uses 4096 because the
original broad demo question exhausted the previous 2048-token output limit.
Both model calls use the same configured limit, and template/slot input budgeting
reserves the actual output allowance. Source/catalog authority is
checked before execution, after execution and when retrieving a completed result;
the cited active chunks are also rechecked before delivery.

The screen submits once and polls every two seconds. Individual HTTP requests
time out at 15 seconds, and transient connection/server failures retry with
backoff without creating another job. Session storage contains only an opaque
ID, payload digest and timestamp. Resubmitting the same question after an
interruption resumes that job during its retention window. It does not provide
a separate saved-question history or automatic restoration of the composer after
a full page reload.

Validation before rollout:

- 271 backend tests, including isolated SQLite lease recovery and cancellation.
- Isolated MySQL schema: two simultaneous claimants produce one claim, duplicate
  acceptance reuses the job, stale publication is blocked, and result columns
  are LONGTEXT. The temporary schema was removed; operational tables were untouched.
- 169 frontend tests, including synthetic polling beyond 90 seconds, lost ACK,
  reconnect, terminal error, scope changes and stale-response suppression.
- Offline production frontend build; targeted lint and whitespace checks.
- Full TypeScript checking has 29 pre-existing errors, identical to the captured
  deployed source baseline. No new type errors were introduced.

Deployment is an overlay of the four answer Python files and built frontend on
the immutable current image. Existing environment, mounts, ports, resource limits,
folder watcher, ingestion worker, documents and indexes are preserved. The prior
image and Vercel deployment remain available for rollback. Rolling back code does
not delete the additive job tables or existing source/index data.

Runtime deployment evidence and sanitized smoke results are kept in the ignored
`.local/answer-jobs-20261018` release directory. Operational source text and
provider credentials must not be copied into Git or diagnostic output.

## Deployment verification

Deployed code commit: `c8004bfa73baef982baed34c06795e3be6cf2ec7`.
Final image: `docmind-ragflow:answer-jobs-long-20261018`,
`sha256:9eef1c3326b1c27d8e5a61548f189787b9abc9937a3aab61e0842334d3f6579f`.
All 288 Python files were checked, preserving the 284 original files outside
the four answer files. Existing documents/indexes, configuration apart from
the declared output allowance, and original host tasks were verified unchanged.
The existing folder catalog remained available with source synchronization enabled.

The original broad smart-factory question completed through the public site in
53.28 seconds, with `partial` coverage, 11 cited passages and 9 distinct document
IDs (some document names are duplicated). Acceptance returned HTTP 202 in 0.203
seconds. Repeated authorized result retrieval returned the same answer, replaying
a completed receipt reused its job, and unauthenticated retrieval returned 401.
This is a bounded smoke test, not a new semantic accuracy score.

A separate installation question demonstrated recovery across a real connectivity
interruption: answer processing took 34.673 seconds and the stored result was eventually
retrieved at 321.68 seconds after 10 transient polling failures. It was not
regenerated. The final broad-question test had no transient polling errors.

During rollout the previous temporary Cloudflare connection returned 530/1033.
The existing temporary-tunnel task was reprovisioned using HTTP/2, retaining its
other settings, and both Vercel API rewrites were updated after direct health
verification. Local and public frontend entrypoints and answer assets match the
built package; database, document engine, Redis and storage health all report OK.
No fixed domain was purchased or provisioned by this change.

An obsolete `context-attempt1` staging folder (36,802,972 bytes) remains because
automatic approval review rejected its deletion with `blocked by policy`.
It is not mounted into the running service. No deletion workaround was attempted.
