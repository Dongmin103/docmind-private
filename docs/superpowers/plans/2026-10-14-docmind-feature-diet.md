# DocMind feature diet implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Track evidence here; do not mark the overall goal complete after a subset.

**Goal:** Remove all approved priority 1, 2 and 3 RAGFlow feature surfaces outside DocMind, including their unused runtime/dependency/build paths.

**Architecture:** Preserve the DocMind document pipeline and extract shared primitives before deleting owning feature packages. Remove entrypoints and references together; no compatibility-only stubs. Preserve stored data and required operations.

**Tech Stack:** Python/Quart, React/TypeScript, Windows workers, Docker/WSL, MySQL, Elasticsearch, Redis, GTE GPU.

**Spec:** PRD.md sections 7, 9, 10 and 14, with approved GTE replacement; user's 2026-10-14 approval of all three priority groups supersedes prior list-only scope and admin-on-demand retention.

## Global constraints

- Preserve cloud sync, source/tenant rights, version fencing, retry/cleanup, selective OCR, PDF/Office/HWP parsers, existing chunking, GTE, C128/prefix2400/Jina v3.5/Top5, answers/citations/history, preview, profile/model settings.
- Preserve original files, DB/ES rows, model cache and volumes. Source deletion is authorized; data deletion is a separate operation.
- Verify actual model/parser bindings and shared consumers before removing provider or storage implementations. Required shared primitives must move, not disappear.
- No blanket dependency install or simultaneous memory-intensive builds on the 16GB host. Keep current service running while editing/testing.
- Preserve unrelated primary checkout modifications. Worktree: `.local/worktrees/parser-cpu-324`, baseline `f388067`.

## Review focus

- Deleted routes must be unreachable through automatic blueprint discovery and backward aliases, not merely absent from menus.
- Cold API import, startup and DB initialization must not import deleted modules.
- Shared document chunk inspection, preview, settings and conversation history remain functional.
- Retired task types/configuration must not create retries or silently report success; inspect existing tasks before rollout.
- Dependency and container image cleanup must reflect removed code; do not claim RAM savings from file counts.

## Tasks and completion evidence

Each task: inspect current consumers; add meaningful boundary regression; observe failure; implement removal/extraction; run focused regressions; review diff; record evidence and commit. Parent owns shared integration files unless explicitly delegated.

- [ ] **1. Administrator tools:** remove `app/admin`, exclusive tests, `web/src/pages/admin` and admin service/types/API constants/routes, 404 return link, startup flags, ports/proxies, Docker COPY and Helm admin resources. Keep `/docmind/admin/*`, shared auth/user/model APIs. Audit Go admin/CLI closure alongside task 10. Verify removed flag rejection and protected routes.
- [ ] **2. Plugins:** remove `agent/plugin`, plugin API, startup loader and exclusive dependencies. Verify no discovered plugin API or startup plugin loader.
- [ ] **3. External messaging:** remove `api/channels`, channel API/service, standalone Discord server and channel UI/settings, startup/shutdown thread, exclusive packages. Keep ordinary conversation/dialog services; retain existing DB records. Verify main API startup/answer/history paths.
- [ ] **4. Generic apps:** remove separate chat/search/chatbot/file screens and exclusive APIs/SDK adapters. Preserve DocMind UI, shared dataset search, chunk inspection, preview, account/profile/model settings; test retained routes and removed aliases.
- [ ] **5. Agents/workflows:** remove editor/templates/share/debug/tools/API/task producers; extract required `agent.component.base`, `agent.canvas.Graph` and LLM primitives consumed by `rag.flow`. Verify ingestion imports and representative PDF/Office/HWP/selective OCR paths.
- [ ] **6. Long-term memory:** remove memory UI/API/services/storage initialization/repair/task branches. Preserve ordinary history retention. Inspect queued work and retained model account deletion path.
- [ ] **7. Graph features:** remove GraphRAG/RAPTOR/mindmap/compilation/wiki/timeline surfaces and algorithms; extract shared limiter/cache helpers from `rag.graphrag.utils`; remove KG initialization and task producers/consumers. Preserve original chunk/vector results.
- [ ] **8. MCP:** remove server/client/settings/API, entrypoint/Compose paths and package after agent and `api_utils` consumers are removed. Verify no public MCP routes or transport processes.
- [ ] **9. Generic dataset/pipeline UI:** remove editors and exclusive configuration while retaining required parser/model/retry/error operations. Validate source selections and embedded chunk inspector.
- [ ] **10. Providers/storage/Go:** read actual settings and model bindings, inventory remaining imports, remove unused provider SDKs/adapters and Go alternate runtime/CLI/build paths. Retain current OCR/VLM, GTE, Jina, answer model, MySQL/ES/storage contract. Update manifests/locks/build recipes and frontend backend selection consistently.
- [ ] **11. Integration:** independent whole-branch review, removed-runtime verification, syntax/import checks, frontend build and controlled type baseline, focused core regressions, clean deployment image, recovery recipe, data integrity and runtime smoke checks. Compare idle/API/system memory under matching conditions if reporting a saving. Document remaining limits; full goal requires evidence for tasks 1–10.

## Ledger

- 2026-10-14: clean worktree at `f388067` confirmed. User authorized all priority groups; prior stop/list-only instruction superseded. Existing deployed image remains `docmind-ragflow:lazy-backends-20261014` until new verified rollout.
- Ruling: do not drop existing feature tables or stored conversation data as part of source cleanup; requested feature diet does not require destructive data migration.
