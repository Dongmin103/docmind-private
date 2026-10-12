# Local GTE deployment plan

User authorized GTE conversion and deployment on 2026-10-12. Existing Docker DocMind on this Windows host is the deployment target.

Architecture: authenticated Windows GPU embedding service exposes the existing OpenAI embeddings contract. Keep Elasticsearch chunk IDs/text/metadata unchanged; prepare q_768_vec alongside q_1024_vec, then switch the dataset and tenant binding together. Keep BGE vectors for rollback. No original parsing or OCR.

- [x] Test and implement API validation, ordering, serialized GPU inference, no silent truncation and bounded batching.
- [x] Start service via Windows scheduled task; verify local and Docker access, restart and concurrent requests.
- [x] Pause ingestion only while migrating; retain previous task state. Verify no active ingestion.
- [x] Re-embed all active chunk bodies and titles using the deployed preprocessing contract; bulk update only q_768_vec. Preserve all original fields and q_1024_vec.
- [x] Validate complete coverage, dimensions, finite vectors and scoped search. Register model and switch tenant/dataset bindings with rollback record.
- [x] Verify search/reranking, embedding new synthetic input, health and restart. Restore ingestion tasks and document results.

Constraints: FP32 eager, CLS + L2, GPU only, 8192 GTE tokens, single GPU execution lock, adaptive batch 8/2/1 by length. Bearer secret stays in ignored local config and database only; never log inputs/vectors/credentials. No Cloudflare calls. Current frontend, parsers, C128, prefix2400 and Jina v3.5 remain.

Ruling: add the new vector field in the existing tenant index instead of introducing a new index-routing abstraction. Existing index routing is tenant-based; two dimension-specific fields isolate migration while preserving chunk identity and rollback. No documents or old vectors are deleted.

Ruling: use the existing feature checkout for new standalone tooling; preserve unrelated untracked reports. Deployment and code change were explicitly authorized, so no extra approval gate.
