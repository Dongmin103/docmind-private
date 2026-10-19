# Local GTE CPU / CUDA service

Authenticated OpenAI-compatible embeddings for the Windows DocMind deployment.
Uses GTE CLS pooling, L2 normalization, 768 dimensions, FP32 eager inference,
and a single execution lock on either device. Choose `device: "cpu"` explicitly
on a server without a GPU. Omitting `device` preserves the existing CUDA default;
CUDA initialization failures never switch silently to CPU.

CUDA defaults: short inputs (up to 1024 tokens) use batches of up to 16 with at
least 85% real tokens after padding; inputs up to 2048 use 2, and longer inputs
use 1. CPU defaults: one input per internal batch and two PyTorch CPU threads.
Output order and token limits are preserved. No silent model-token truncation.
Inputs over 8192 model tokens fail with HTTP 400; an OOM splits the batch and a
single-input OOM returns 503. Request bodies, credentials and vectors are not logged.

Run wrapper/engine logic tests without downloading weights:

```powershell
python -m unittest discover -s tools/local_embedding -p test_service.py
```

Runtime configuration is an ignored, ACL-restricted JSON file:

```json
{
  "api_key": "REPLACE_WITH_RANDOM_SECRET",
  "model_path": "ABSOLUTE_LOCAL_MODEL_SNAPSHOT",
  "code_revision": "40ced75c3017eb27626c9d4ea981bde21a2662f4",
  "device": "cpu",
  "cpu_threads": 2,
  "short_batch": 1,
  "bind": "0.0.0.0",
  "port": 18882
}
```

For a fresh server, copy the existing embedding API key into this local file and
use the same key in DocMind's embedding provider configuration. `model_path` is
the new server's own GTE model directory, not the old server's path. This file
does not configure source document roots or copy search indexes.

`device` accepts only `cpu` or `cuda`. `cpu_threads` must be an integer from 1 to
256; it sets PyTorch's CPU thread count (default 2 on CPU, 6 on CUDA).
`short_batch` must be an integer from 1 to 64 (default 1 on CPU, 16 on CUDA).
It also caps the two-input medium bucket. Keep the CPU defaults initially and
increase only after checking the target server's memory and latency. HTTP requests
still accept up to 64 inputs; the service breaks these into internal batches.

Launch with `python service.py --config ABSOLUTE_CONFIG_PATH`. Pre-cache both the
model snapshot and trusted model implementation: startup is local-files-only.
Model revision tested: `9bbca17d9273fd0d03d5725c7a4b0f6b45142062`.
Existing CUDA runtime tested: Python 3.11.16, torch 2.8.0+cu126, transformers 4.57.1,
tokenizers 0.22.2, numpy 2.4.6, safetensors 0.8.0, GTX 1080 8 GiB.

CPU inference requires a CPU-capable PyTorch installation; CUDA toolkit and an
NVIDIA GPU are unnecessary when `device` is `cpu`. Use a dedicated environment
on the new server. The wrapper tests above use simulated model/runtime objects;
they verify device selection, no CPU access to CUDA, FP32/CLS/L2 contracts, input
limits, output order, serialization and CPU/CUDA memory-failure handling. Actual
throughput and long-document memory limits must be measured on the target server.

GET `/health` is unauthenticated and contains only readiness/model metadata.
Its `device` field reports the selected device (`cpu` or `cuda`). Startup performs
an embedding before opening the HTTP listener, so a model/device failure prevents
the service from reporting ready.
POST `/v1/embeddings` requires `Authorization: Bearer SECRET`; accepts model
`gte-multilingual-base`, one string or 1–64 strings, float encoding, dimensions 768.
Request bodies are capped at 8 MiB. An embedding queue wait can take up to 180 seconds;
that is not a guarantee that an upstream shorter timeout can accommodate arbitrary
long-input traffic, particularly on CPU. Start upstream embedding HTTP concurrency
at 1 and external batch size at 1 for a new CPU deployment. The existing CUDA
deployment's embedding HTTP concurrency is 2 and external batch size is 32. The selected
document pool uses 4 slots, 2 parser processes and a 4-core parser quota.

The installed task, private runtime paths, migration and measured load limits are
documented in `docs/LOCAL-GTE-DEPLOYMENT-2026-10-12.md`. Keep the experiment venv
and pinned Hugging Face model/code caches: the installed service depends on them.
Do not delete these as unused benchmark artifacts.
