# Embedding throughput implementation plan

**Spec:** `docs/superpowers/specs/2026-10-12-embedding-throughput-design.md`
**Goal:** Implement priorities 5–7 with preserved vector semantics and measurable request reduction.
**Architecture:** Existing EmbeddingService -> LLMBundle -> provider adapter; bounded async scheduling around synchronous encode calls.
**Stack:** Python, asyncio, numpy, pytest, existing OpenAI-compatible adapters.
**Global constraints:** Follow root/app AGENTS.md; preserve retrieval/model/chunking contracts; synthetic data only; no running service/release/env changes; no secrets or source text in new telemetry; retain unrelated untracked files. Work on current `perf/indexing-throughput` branch.
**Review focus:** Effective adapter batching rather than service-only changes; provider limits and subclass isolation; title/body truncation parity; response cardinality/order/dimension; token accounting and LLMBundle thread safety; cancellation and limiter lifetime; deterministic failure behavior.

## Task 1 — Coupled embedding implementation and regression tests
- [x] Read full embedding service/utils, LLMBundle.encode, adapter and shared limiter/thread_pool_exec definitions. Determine supported settings without reading secrets into output.
- [x] Write failing focused tests in existing embedding service and adapter suites for merged title/body calls, configurable effective batching, ordered out-of-order completion, numerical parity, usage totals, bounded concurrency, failure/cancellation and invalid settings.
- [x] Modify `app/rag/svr/task_executor_refactor/embedding_service.py`, `app/rag/llm/embedding_model.py`, `app/common/settings.py`; narrowly adjust LLMBundle/context only if necessary for the contract. Add validated settings, conservative defaults and provider caps. Do not broaden unrelated adapters.
- [x] Preserve existing shared limiter and synchronous-call lifecycle, including outstanding calls on failure/cancellation; default concurrency 1, configurable 2 after bounded verification. Count the title within batch size.
- [x] Add aggregate/batch metadata needed to distinguish limiter wait from encode time without payloads or URLs. Keep instrumentation small and avoid a metrics framework.
- [x] Run focused suites with real numpy/asyncio and actual changed code, not merely mock assertions; document dependency limitations. Check lint/diff and commit this task only.

## Task 2 — Synthetic benchmark, documentation, independent review
- [x] Implementation agent supplies reproducible synthetic benchmark comparing original 16+separate title, merged title, batch32/64, concurrency2; validates exact vector alignment and usage; records API call counts and maximum in-flight calls. Use no live API or operational services.
- [x] Record settings, scoped support, rollback, test commands and measured limitations in `docs/EMBEDDING-THROUGHPUT-2026-10-12.md`.
- [x] Controller creates review package and dispatches fresh reviewer for specification and quality, then sends fixes to implementation agent. Verify final results before reporting.

Task 1 and benchmark implementation in Task 2 are one implementer assignment because they share the scheduling/adapter contract. Controller owns review and final verification. No product changes by controller.

## Preflight
| Shared interface | Required agreement | Resolution |
| --- | --- | --- |
| Service / adapter batch size | Title counts toward request size; inner adapter may split further | Assert actual provider call count, not just service encode count |
| Scheduler / LLMBundle / limiter | Async cancellation cannot stop a running thread | Test draining and limiter retention; serialize unsupported shared state |
| Defaults / benchmark | Synthetic latency is not live provider evidence | Keep concurrency1 and legacy provider defaults until deployed measurements |

## Completion

Implemented in afdd062; independent review fixes in 3a59e0b and 3ea2633. GPT-6.1 Sol / xhigh implemented and independently reviewed the change. Final review approved with no unresolved findings. Standalone throughput tests: 24 passed; combined focused suite: 85 passed. Scoped lint, diff checks and retired-runtime guard passed. Synthetic results and exact commands: docs/EMBEDDING-THROUGHPUT-2026-10-12.md. Source-only completion; no deployment or live provider throughput measurement. Defaults remain batch16/concurrency1.
