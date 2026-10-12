# Local GTE GPU service

Authenticated OpenAI-compatible embeddings for the Windows DocMind deployment.
Uses GTE CLS pooling, L2 normalization, 768 dimensions, FP32 eager CUDA inference,
and a single GPU execution lock. No CPU fallback or silent model-token truncation.
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
  "bind": "0.0.0.0",
  "port": 18882
}
```

Launch with `python service.py --config ABSOLUTE_CONFIG_PATH`. Pre-cache both the
model snapshot and trusted model implementation: startup is local-files-only.
Model revision tested: `9bbca17d9273fd0d03d5725c7a4b0f6b45142062`.
Runtime tested: Python 3.11.16, torch 2.8.0+cu126, transformers 4.57.1,
tokenizers 0.22.2, numpy 2.4.6, safetensors 0.8.0, GTX 1080 8 GiB.

GET `/health` is unauthenticated and contains only readiness/model metadata.
POST `/v1/embeddings` requires `Authorization: Bearer SECRET`; accepts model
`gte-multilingual-base`, one string or 1–64 strings, float encoding, dimensions768.
Request bodies are capped at 8 MiB. A GPU queue wait can take up to180 seconds;
that is not a guarantee that an upstream shorter timeout can accommodate arbitrary
long-input traffic. Current ingestion concurrency is2, external batch size32.

The installed task, private runtime paths, migration and measured load limits are
documented in `docs/LOCAL-GTE-DEPLOYMENT-2026-10-12.md`. Keep the experiment venv
and pinned Hugging Face model/code caches: the installed service depends on them.
Do not delete these as unused benchmark artifacts.
