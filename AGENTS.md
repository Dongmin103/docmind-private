# DOCMIND product repository

Read README.md for the Windows/WSL CPU deployment contract. Keep the product package allowlist synchronized with runtime/build dependencies.

- Do not modify or restart existing deployments or operational document/index data without explicit authorization.
- Never commit credentials, plaintext documents, indexed data, model weights, generated settings, installed runtimes, or logs. Generated settings belong in ignored .local/. Transfer credentials with the encrypted settings archive and separately supplied key.
- New CPU deployment disables OCR. CPU inference must not require CUDA. Preserve 768-dimensional GTE embeddings and existing chunking/reranking behavior.
- Windows workers run the approved uEncryptor2 executable. Preserve signed executable verification, source boundaries, cleanup receipts and plaintext cleanup. Do not execute it inside Linux containers.
- Source roots are explicit configuration. Do not assume old Windows paths or claim VDI REST login/folder authorization is implemented.
- Keep model settings out of user navigation. Preserve document search, synchronization and previews. Keep necessary RAGFlow-derived runtime and licenses, without generic SDKs/examples/Helm/upstream documentation.
- Test with isolated synthetic data. Do not claim a complete target-server deployment from unit tests or a frontend build alone.
