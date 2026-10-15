# DocMind answer and OCR workspace integration

This change integrates the missing DocMind UI and answer API into the optimized
feature-diet branch. It is a built release candidate, not a production deployment.

## Integration sources

- Base: `ecb9e75` on `perf/parser-cpu-324`.
- UI: `feat/ocr-review-panel` (`82f2462`), including AI questions, evidence,
  search preview, folder scope picker, and OCR review dashboard.
- Answer service and route: `feat/local-llm-context` (`e5af52e`), including its
  bounded provider request and citation/scope/version checks.
- Kept the current neutral chunk inspector and workspace settings. The citation
  reader now uses the existing authenticated chunk-detail endpoint through
  `chunk-service`, rather than restoring the retired knowledge management API.
- Kept ingestion, embedding, preview service, package manifests and deployment
  configuration unchanged. No retired admin server or agent workflow is restored.

The regression came from building an optimization branch without the separately
developed DocMind features. The October 13 parser deployment had the answer route;
the October 14 cleanup deployment already lacked it and used the older UI. The
October 15 clean image also omitted the answer service. Reverting only the latest
image would therefore not restore the complete feature set.

## Verification

- Frontend: 16 suites, 176 tests passed, covering answer/search separation,
  citations, HTML sanitization, stale data, OCR consent, scope and inspection.
- Backend answer service/provider/real Quart route with isolated auth/storage
  boundaries: 220 tests passed. These are not live database or model tests.
- Production frontend build passed using the existing clean lockfile-installed
  dependency volume. Built product sources match the final source. The only
  post-build change was a test-only type import; its 36 tests passed again.
- TypeScript reports the same 29 existing diagnostics as the feature-diet
  baseline; zero added diagnostics. The entire type check is not clean.
- Focused frontend lint and retired-runtime reference check passed.
- Independent review found one stale test type import; corrected and verified.
- The built UI was exercised in a loopback-only synthetic HTTP harness: AI
  question/answer/citation display, existing chunk-detail request, general search,
  temporary HWP preview panel, OCR review list, cancel without submitting, and
  explicit OCR submission followed by refreshed processing status.
- The synthetic harness initially expected the wrong OCR payload field; corrected
  it to the existing `expected_source_version_id`, consent and idempotency-header
  contract. No product change was made for that harness error.
- Settings navigation remains available. Its synthetic server does not implement
  the full provider-management API; this is not a live settings regression test.

## Remaining release checks

The configured real model listed successfully (HTTP 200, 0.278 seconds), but the
restored provider's synthetic one-evidence generation reached
`DOCMIND_LLM_TIMEOUT` after 85.304 seconds. A preliminary probe had been cancelled
by a test-only 50-second limit; the final probe used the unchanged service limits.
The model's two slots were both busy, with 49,294 and 48,015 input tokens reported
in metadata. Queueing is a likely explanation, not a confirmed causal trace.
No existing model job was interrupted and no timeout/model setting was changed.
Successful real answer generation therefore remains unverified. The user
explicitly directed that model-server load be treated separately and not block
our restoration/release preparation. No timeout increase is included.

No production containers, Vercel deployment, operational documents or indexes were
modified. The initial UI harness was chosen while host free memory was about
1.3 GiB. A later WSL check showed about 6.9 GiB available within the VM, so after
the build stopped an isolated, resource-limited runtime with disposable databases
was started. It has no operational data mounts or external model access. Actual
source synchronization, OCR inference, decryption and operational
database-to-model end-to-end behavior were not rerun.

## Built release candidate

- Source: `f4f8efbc7a31648c43a4bb4f49970d4c8f3ea882`.
- Image: `docmind-ragflow:diet-f4f8efbc-722826b3`.
- Digest: `sha256:b1bfcd0c565027129a0431b277c79cb7a163f3d19aa72abea517429e889e5e26`.
- Built the committed `app/Dockerfile` from a Git archive; no working-tree overlay.
- Build succeeded; its frontend build took 45.17 seconds. BuildKit was stopped
  before the independent runtime check.
- All 394 frontend assets exactly match the browser-tested build. Answer route,
  answer service, ingestion, preview, embedding and dependency file hashes match
  the committed source. Python sources parsed successfully inside the image.
- Independent runtime checks: 35 passed, zero failed, against disposable MySQL,
  Elasticsearch, Redis and MinIO. The real restored answer route rejects anonymous
  callers and client tenant overrides, and returns insufficient evidence for an
  authenticated empty catalog. Retained profile/model/chunk/history endpoints and
  removed endpoint boundaries passed as well.
- Browser verification against that actual image confirmed AI submission to the
  real answer route, the OCR dashboard and model/provider settings. A unique
  `.localhost` hostname was used after session interference on bare `127.0.0.1`;
  the isolated-host flow passed without an authentication code change.

For rollout, use this single release and preserve the existing optimized runtime
settings, then perform a bounded real-service smoke check. File hashes
or a generic search check alone do not prove that AI answers and OCR are present.

Local evidence is in `.local/docmind-feature-integration/`: build/test logs,
typecheck comparison, protected-file hashes, synthetic browser request log,
screenshots and actual configured-model probe results. The accompanying JSON
records the final model probe outcome separately from synthetic UI checks.
