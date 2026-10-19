# DOCMIND product repository

Read README.md and deployment/AGENT-HANDOFF.md for the deployment intent, target-environment assessment and CPU/shared-session contract. Keep the product package allowlist synchronized with runtime/build dependencies.

- Do not modify or restart existing deployments or operational document/index data without explicit authorization.
- The user explicitly authorized bundling the existing encrypted settings archive and its recovery.key in this PRIVATE repository on 2026-10-19. Only the two exact deployment/install-inputs files are approved credential inputs. Keep this repository and its packages private; do not print decrypted credentials or copy them elsewhere in source. Other credentials, plaintext documents, indexed data, weights, generated settings, runtimes and logs remain excluded. Generated settings belong in ignored .local/.
- New CPU deployment disables OCR. CPU inference must not require CUDA. Preserve 768-dimensional GTE embeddings and existing chunking/reranking behavior.
- Preserve automatic entry through the existing shared session. workspace_email identifies an internal workspace account; do not generate a user-facing login password/file or add email/password login instructions.
- The user's only application configuration is deployment/source-folders.txt. Assess the actual target OS, Docker runtime, decryptor, permissions and network using README.md; do not assume the target matches the current host or ask the user to reconfigure models/API keys/internal accounts.
- Deploy the pinned PRIVATE Release images with runtime_images.py and compose --no-build --pull never. Model weights are baked in the CPU image. Do not add target-server source/UI builds or Hugging Face downloads to the default installation.
- Multiple external source roots use stable path identities; reordering must not reassign existing documents. Apply changes through the installer after its own workers finish cleanup. Pausing a removed root does not authorize deleting original files or indexed data.
- Windows workers run the approved uEncryptor2 executable. Preserve signed executable verification, source boundaries, cleanup receipts and plaintext cleanup. Do not execute it inside Linux containers.
- Source roots are explicit configuration. Do not assume old Windows paths or claim VDI REST login/folder authorization is implemented.
- Keep model settings out of user navigation. Preserve document search, synchronization and previews. Keep necessary RAGFlow-derived runtime and licenses, without generic SDKs/examples/Helm/upstream documentation.
- Test with isolated synthetic data. Do not claim a complete target-server deployment from unit tests or a frontend build alone.
