#!/usr/bin/env python3
"""Build a DocMind product source archive from Git-tracked working-tree files.

The allowlist below is deliberately narrower than the development repository.
Secrets, installed runtimes, model weights and indexed documents are separate
deployment inputs. No repository, service, Git index or remote is modified.
Run ``python tools/deployment/build_product_package.py --self-test`` to test
the selection and archive integrity checks without building the application.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import tarfile
import tempfile
import unittest
import zipfile


SCHEMA_VERSION = 1
MANIFEST_NAME = "PRODUCT-MANIFEST.json"
ROOT_FILES = {"README.md", "AGENTS.md", "LICENSE", "NOTICE", "NOTICE.txt", "THIRD_PARTY_NOTICES.md", ".gitignore", ".gitattributes"}
APP_FILES = {"Dockerfile", ".dockerignore", "pyproject.toml", "uv.lock", "LICENSE", "NOTICE", "NOTICE.txt", "THIRD_PARTY_NOTICES.md", "README.md"}
DEPLOYMENT_FILES = {"prepare_cpu_server.py", "bootstrap_cpu_workspace.py", "Start-DocMindCpuServer.ps1", "Install-DocMindCpuServer.ps1", "Register-DocMindCpuWorkers.ps1", "build_product_package.py"}
RUNTIME_DIRECTORIES = {"api", "common", "rag", "deepdoc", "conf"}
DOCKER_FILES = {"entrypoint.sh", "service_conf.yaml.template", "init.sql"}
PRODUCT_SCRIPTS = {"verify_removed_runtime.py"}
MIGRATION_FILES = {"run_migrations.sh", "mysql_migration.py", "db_schema_sync.py", "rename_models_providers.py"}
WINDOWS_FILES = {
    "DocMindDecryptConcurrency.ps1", "DocMindDecryptGate.ps1", "DocMindFolderSupervisor.cs",
    "DocMindIngestionPool.ps1", "DocMindSyncCollector.cs", "DocMindSyncQueue.cs", "DocMindSyncTransport.ps1",
    "DocMindUEncryptorHostWorker.Common.ps1", "DocMindUnicodeCaseFold.cs",
    "Get-DocMindReconciliationTaskPlan.ps1", "Get-DocMindSourceMetadataManifest.ps1",
    "Invoke-DocMindSourceReconciliation.ps1", "Register-DocMindHostSyncTasks.ps1",
    "Register-DocMindScopedSourceTasks.ps1", "Start-DocMindIngestionPool.ps1",
    "Start-DocMindUEncryptorHostWorker.ps1", "Sync-DocMindSourceFolders.ps1", "Watch-DocMindEncryptedSources.ps1",
    "docmind-host-worker.config.example.json", "generate_sync_casefold.py", "initialize_auth_keys.py",
}
FORBIDDEN_PARTS = {
    ".git", ".local", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "dist", "build", "coverage", "htmlcov", "secrets", "private",
    "models", "logs", "runtime", "output", ".storybook", "stories", "test", "tests", "__tests__",
}
FORBIDDEN_SUFFIXES = {
    ".pyc", ".pyo", ".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3", ".db",
    ".safetensors", ".gguf", ".onnx", ".ckpt", ".pt", ".pth", ".bin", ".log",
    ".zip", ".tar", ".gz", ".7z", ".exe", ".dll",
}
REQUIRED_FILES = {
    "README.md", "app/LICENSE", "app/THIRD_PARTY_NOTICES.md", "app/Dockerfile", "app/pyproject.toml", "app/uv.lock",
    "app/api/ragflow_server.py", "app/docker/entrypoint.sh", "app/docker/service_conf.yaml.template",
    "app/docker/init.sql", "app/tools/scripts/run_migrations.sh", "app/tools/scripts/mysql_migration.py",
    "app/web/package.json", "app/web/package-lock.json", "app/web/src/main.tsx",
    "app/parser_services/kordoc/Containerfile", "app/parser_services/preview/Containerfile",
    "tools/local_embedding/service.py", "tools/local_embedding/Containerfile.cpu",
    "tools/local_embedding/requirements.cpu.txt", "tools/local_embedding/download_model.py",
    "tools/deployment/build_product_package.py", "tools/deployment/prepare_cpu_server.py",
    "tools/deployment/bootstrap_cpu_workspace.py", "tools/deployment/Start-DocMindCpuServer.ps1",
    "tools/deployment/Install-DocMindCpuServer.ps1", "tools/deployment/Register-DocMindCpuWorkers.ps1",
    "deployment/cpu/server.example.json",
}
DOCKER_CONTEXTS = {
    "app/Dockerfile": "app",
    "app/parser_services/kordoc/Containerfile": "app/parser_services/kordoc",
    "app/parser_services/preview/Containerfile": "app/parser_services",
    "tools/local_embedding/Containerfile.cpu": "tools/local_embedding",
}


def safe_relative(name: str) -> PurePosixPath:
    """Reject archive traversal, ambiguous Windows names and absolute paths."""
    path = PurePosixPath(name)
    if name in {"", ".", ".."} or "\\" in name or ":" in name or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe relative path: {name!r}")
    if path.as_posix() != name or any(p.endswith((" ", ".")) for p in path.parts):
        raise ValueError(f"Noncanonical relative path: {name!r}")
    return path


def exclusion_reason(name: str) -> str | None:
    path = safe_relative(name)
    parts = path.parts
    lower = tuple(p.lower() for p in parts)
    base = path.name.lower()
    blocked_parts = FORBIDDEN_PARTS - {"models"} if parts[:3] == ("app", "conf", "models") else FORBIDDEN_PARTS
    if set(lower) & blocked_parts or path.suffix.lower() in FORBIDDEN_SUFFIXES:
        return "runtime data, secrets, weights, generated files or tests"
    if base == ".env" or (base.startswith(".env.") and not base.endswith(".example")) or base.endswith(".env"):
        return "environment credentials"
    if base.startswith(("test_", "test-")) or ".test." in base or ".stories." in base:
        return "development tests or stories"
    if name in ROOT_FILES:
        return None
    if parts[:2] == ("deployment", "cpu"):
        return None if path.name in {"server.example.json", "README.md"} else "deployment runtime configuration"
    if parts[:2] == ("tools", "deployment"):
        return None if path.name in DEPLOYMENT_FILES else "outside product deployment tool allowlist"
    if parts[:2] == ("tools", "local_embedding"):
        return None if base in {"service.py", "containerfile.cpu", "requirements.cpu.txt", "download_model.py", "readme.md"} else "embedding experiments"
    if parts[:2] == ("tools", "windows"):
        return None if path.name in WINDOWS_FILES else "Windows development/recovery tools"
    if len(parts) < 2 or parts[0] != "app":
        return "outside product source allowlist"
    if len(parts) == 2:
        return None if parts[1] in APP_FILES else "upstream/development root files"
    owner = parts[1]
    if owner in RUNTIME_DIRECTORIES:
        if base.startswith("readme") or base in {"agents.md", "claude.md"}:
            return "upstream development documentation"
        return None
    if owner == "web":
        if name == "app/web/src/pages/user-setting/constants.tsx":
            return None  # Shared TenantRole values remain imported by a retained layout.
        if parts[2:4] in {("src", "stories")} or parts[2:5] in {("src", "pages", "user-setting"), ("src", "pages", "login-next")}:
            return "unreachable upstream screens or stories"
        if parts[2:5] == ("src", "components", "api-service"):
            return "unreachable upstream API documentation UI"
        if base.startswith("jest") or base in {"readme.md", "agents.md", "claude.md", "skills-lock.json"}:
            return "frontend development metadata"
        return None
    if owner == "docker":
        return None if path.name in DOCKER_FILES or parts[2] == "nginx" else "unselected Compose and development configurations"
    if owner == "parser_services" and parts[2] in {"kordoc", "preview"}:
        return None if not base.startswith("readme") else "parser development documentation"
    if owner == "scripts":
        return None if path.name in PRODUCT_SCRIPTS else "benchmarks and historical deployment scripts"
    if parts[1:3] == ("tools", "scripts"):
        return None if path.name in MIGRATION_FILES else "upstream tooling documentation"
    if owner == "ragflow_deps" and path.name in {"Dockerfile", "download_deps.py"}:
        return None
    return "upstream SDK, documentation, examples, Helm or other unselected source"


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(["git", "ls-files", "--cached", "-z"], cwd=root, check=True, capture_output=True)
    return sorted(set(result.stdout.decode("utf-8").split("\0")) - {""})


def read_source(root: Path, name: str) -> bytes:
    relative = safe_relative(name)
    path = root.joinpath(*relative.parts)
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Source link escapes or is not a regular product file: {name}")
    if not path.is_file():
        raise ValueError(f"Tracked product file is missing: {name}; stage intentional deletions before packaging")
    # Ancestor links can change what Git's tracked name means, even within root.
    if any(parent.is_symlink() for parent in path.parents if parent != root and parent.is_relative_to(root)):
        raise ValueError(f"Symlinked source directory: {name}")
    return path.read_bytes()


def validate_copy_inputs(files: dict[str, bytes]) -> None:
    """Check local COPY/ADD inputs for every selected production Docker build."""
    for dockerfile, context in DOCKER_CONTEXTS.items():
        if dockerfile not in files:
            raise ValueError(f"Required Dockerfile not packaged: {dockerfile}")
        text = files[dockerfile].decode("utf-8-sig").replace("\\\n", " ").replace("\\\r\n", " ")
        for line in text.splitlines():
            fields = line.strip().split(None, 1)
            if len(fields) != 2 or fields[0].upper() not in {"COPY", "ADD"}:
                continue
            instruction = fields[1]
            if instruction.startswith("--from="):
                continue  # Images and earlier stages are intentionally external.
            while instruction.startswith("--"):
                instruction = instruction.split(None, 1)[1]
            tokens = json.loads(instruction) if instruction.startswith("[") else shlex.split(instruction)
            for source in tokens[:-1]:
                if source.startswith(("https://", "http://")):
                    continue
                source = source.rstrip("/").removeprefix("./")
                if "$" in source:
                    raise ValueError(f"Cannot statically validate variable COPY input in {dockerfile}: {source}")
                prefix = f"{context}/{source}"
                safe_relative(prefix)
                if not any(name == prefix or name.startswith(prefix + "/") or fnmatch.fnmatchcase(name, prefix) for name in files):
                    raise ValueError(f"Docker build input excluded/missing: {dockerfile}: {source}")


def make_archive(root: Path, output: Path, archive_format: str) -> dict:
    root = root.resolve()
    local = (root / ".local").resolve()
    output = output.resolve()
    if not local.is_relative_to(root) or not output.is_relative_to(local) or output == local:
        raise ValueError("Product archive output must remain below the repository's .local/ directory")
    if output.exists():
        raise ValueError(f"Refusing to overwrite archive: {output}")
    selected: dict[str, bytes] = {}
    excluded = []
    case_names = set()
    for name in tracked_files(root):
        reason = exclusion_reason(name)
        if reason:
            excluded.append({"path": name, "reason": reason})
            continue
        if name.casefold() in case_names:
            raise ValueError(f"Case-insensitive archive filename collision: {name}")
        case_names.add(name.casefold())
        selected[name] = read_source(root, name)
    missing = REQUIRED_FILES - selected.keys()
    if missing:
        raise ValueError("Required tracked product files missing (stage new files first): " + ", ".join(sorted(missing)))
    validate_copy_inputs(selected)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    changes = subprocess.run(["git", "status", "--porcelain", "-z", "--untracked-files=no"], cwd=root, check=True, capture_output=True).stdout
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "product": "DOCMIND CPU source package",
        "source_revision": revision,
        "tracked_worktree_modified": bool(changes),
        "source_policy": "Git-tracked working-tree bytes; explicit product allowlist; no Git history",
        "runtime_inputs": "Credentials, models, decryptor executable and new source roots are configured separately. No database or vector index is bundled.",
        "docker_contexts": DOCKER_CONTEXTS,
        "files": [{"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in sorted(selected.items())],
        "excluded": excluded,
    }
    selected[MANIFEST_NAME] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".product-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
        if archive_format == "zip":
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in sorted(selected.items()):
                    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = (0o100755 if name.endswith(".sh") else 0o100644) << 16
                    archive.writestr(info, data)
        else:
            with tarfile.open(temporary, "w:gz") as archive:
                for name, data in sorted(selected.items()):
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    info.mode = 0o755 if name.endswith(".sh") else 0o644
                    archive.addfile(info, io.BytesIO(data))
        verify_archive(temporary, archive_format)
        # Publish without overwriting another build's completed archive.
        with temporary.open("rb") as source, output.open("xb") as destination:
            while chunk := source.read(1024 * 1024):
                destination.write(chunk)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {"archive": str(output), "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "files": len(manifest["files"]), "excluded": len(excluded)}


def verify_archive(path: Path, archive_format: str) -> None:
    if archive_format == "zip":
        with zipfile.ZipFile(path) as archive:
            if len(set(archive.namelist())) != len(archive.namelist()):
                raise ValueError("Duplicate archive entries")
            entries = {name: archive.read(name) for name in archive.namelist()}
    else:
        with tarfile.open(path, "r:gz") as archive:
            entries = {}
            for member in archive.getmembers():
                if member.name in entries:
                    raise ValueError("Duplicate archive entries")
                if not member.isfile():
                    raise ValueError(f"Unexpected non-file archive entry: {member.name}")
                entries[member.name] = archive.extractfile(member).read()
    for name in entries:
        safe_relative(name)
    manifest = json.loads(entries.pop(MANIFEST_NAME))
    expected = {record["path"]: record for record in manifest["files"]}
    if entries.keys() != expected.keys():
        raise ValueError("Archive file list differs from product manifest")
    for name, data in entries.items():
        if len(data) != expected[name]["bytes"] or hashlib.sha256(data).hexdigest() != expected[name]["sha256"]:
            raise ValueError(f"Archive file hash mismatch: {name}")


class PackagePolicyTests(unittest.TestCase):
    def test_product_inputs_retained(self):
        for name in REQUIRED_FILES | {"app/rag/prompts/citation.md", "app/conf/models/llm_factory.json", "app/web/public/tokenizer.json", "app/parser_services/kordoc/patches/kordoc@4.15.7.patch"}:
            self.assertIsNone(exclusion_reason(name), name)

    def test_non_product_inputs_excluded(self):
        for name in {"app/sdk/python/ragflow_sdk/session.py", "app/docs/references/http_api_reference.md", "app/helm/values.yaml", "app/example/sample.py", "app/README_ko.md", "app/test/unit_test/a.py", "app/web/src/stories/a.tsx", "app/web/src/pages/user-setting/index.tsx", "app/web/src/pages/login-next/index.tsx", "app/web/src/components/api-service/chat-overview-modal/api-content.tsx", "app/api/test_user.py", "app/web/src/x.test.tsx", "app/web/dist/a.js", "app/conf/private.key", "app/docker/.env", "app/docker/.env.production", "app/docker/docker-compose-windows-dev-surya-gpu.yml", "deployment/host-roles/gte/runtime.py", ".local/secret.json", "tools/local_embedding/pipeline_probe.py", "restore_backup.py", "docs/FEATURE-DIET-2026-10-15.md"}:
            self.assertIsNotNone(exclusion_reason(name), name)

    def test_traversal_rejected(self):
        for name in {"/etc/passwd", "../private.key", "app/../../x", "C:/private.key", "app\\conf\\x", "app//a.py", "app/a. /x"}:
            with self.assertRaises(ValueError, msg=name):
                safe_relative(name)

    def test_copy_dependencies_and_stages(self):
        files = {dockerfile: b"FROM base\nCOPY --from=builder /external /x\n" for dockerfile in DOCKER_CONTEXTS}
        files["app/Dockerfile"] += b"COPY api api\nCOPY docs docs\n"
        files["app/api/ragflow_server.py"] = b""
        with self.assertRaisesRegex(ValueError, "docs"):
            validate_copy_inputs(files)
        files["app/Dockerfile"] = files["app/Dockerfile"].replace(b"COPY docs docs\n", b"")
        validate_copy_inputs(files)

    def test_source_escape_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                read_source(root, "../escape")
            with self.assertRaises(ValueError):
                read_source(root, "missing.py")

    def test_manifest_detects_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.zip"
            manifest = {"files": [{"path": "README.md", "bytes": 2, "sha256": hashlib.sha256(b"ok").hexdigest()}]}
            for payload, valid in [(b"ok", True), (b"xx", False)]:
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr(MANIFEST_NAME, json.dumps(manifest))
                    archive.writestr("README.md", payload)
                if valid:
                    verify_archive(path, "zip")
                else:
                    with self.assertRaisesRegex(ValueError, "hash mismatch"):
                        verify_archive(path, "zip")

    def test_full_package_uses_only_tracked_allowlisted_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
            for name in REQUIRED_FILES | {"app/sdk/unwanted.py"}:
                source = root / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b"source fixture\n")
            for dockerfile in DOCKER_CONTEXTS:
                (root / dockerfile).write_bytes(b"FROM example\nCOPY --from=builder /external /external\n")
            subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=Package test", "-c", "user.email=test@internal.invalid", "commit", "--quiet", "-m", "fixture"], cwd=root, check=True, capture_output=True)
            # This path is allowlisted but intentionally untracked.
            untracked = root / "tools/windows/DocMindDecryptGate.ps1"
            untracked.parent.mkdir(parents=True, exist_ok=True)
            untracked.write_bytes(b"untracked fixture")
            for archive_format in ("zip", "tar.gz"):
                output = root / ".local" / f"product.{archive_format}"
                result = make_archive(root, output, archive_format)
                self.assertEqual(result["files"], len(REQUIRED_FILES))
                verify_archive(output, archive_format)
                with self.assertRaisesRegex(ValueError, "overwrite"):
                    make_archive(root, output, archive_format)
            with self.assertRaisesRegex(ValueError, "below"):
                make_archive(root, root / "outside.zip", "zip")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, help="New archive below <root>/.local/ (never overwritten)")
    parser.add_argument("--format", choices=("zip", "tar.gz"), default="zip")
    parser.add_argument("--verify", type=Path, help="Verify an existing package against its embedded hashes")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PackagePolicyTests))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    if args.verify:
        verify_archive(args.verify, args.format)
        print("Product archive file hashes verified.")
        return
    output = args.output or args.root / ".local" / "packages" / ("docmind-cpu-source.zip" if args.format == "zip" else "docmind-cpu-source.tar.gz")
    print(json.dumps(make_archive(args.root, output, args.format), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
