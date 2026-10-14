#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

"""
Task Handler Module.

Provides [`TaskHandler`](rag/svr/task_executor_refactor/task_handler.py:56) as the main entry point
for handling document processing tasks with refactored, testable methods.
"""

import asyncio
import logging
import json
from contextlib import nullcontext
from datetime import UTC, datetime

from timeit import default_timer as timer
from typing import Callable, Dict, List, Optional

from api.db.services.document_service import DocumentService
from api.db.joint_services.tenant_model_service import (
    get_tenant_default_model_by_type,
    resolve_model_config,
    get_model_config_by_id,
)
from api.db.services.llm_service import LLMBundle
from api.db.services.task_service import abort_doc_chunking_counter
from common.constants import LLMType
from common.exceptions import TaskCanceledException
from common.connection_utils import timeout
from common.misc_utils import thread_pool_exec
from common.storage_attempt_audit import stage_scope
from rag.nlp import search
from rag.utils.chunk_id import make_chunk_id
from rag.svr.task_executor_refactor.constants import CANVAS_DEBUG_DOC_ID
from rag.svr.task_executor_refactor.chunk_service import ChunkService
from rag.svr.task_executor_refactor.dataflow_service import BillingHook, DataflowService
from rag.svr.task_executor_refactor.embedding_service import EmbeddingService
from rag.svr.task_executor_refactor.post_processor import PostProcessor
from rag.svr.task_executor_refactor.task_context import TaskContext
from api.db.services.file2document_service import File2DocumentService
from rag.prompts.generator import run_toc_from_text
from common import settings


class TaskHandler:
    """Main task handler for document processing.

    This class orchestrates the entire document processing pipeline:
    1. Task type detection (dataflow, standard)
    2. Model binding (embedding, chat)
    3. Chunk building
    4. Embedding
    5. Indexing
    6. Post-processing (TOC, table metadata)

    All intermediate results are recorded via RecordingContext for comparison.
    """

    def __init__(
        self,
        ctx: TaskContext,
        billing_hook: Optional[BillingHook] = None,
    ):
        """Initialize TaskHandler.

        Args:
            ctx: TaskContext containing task configuration and execution resources.
            billing_hook: Optional billing hook for pipeline success/error callbacks.
        """
        self._task_context = ctx
        self._billing_hook = billing_hook

    @staticmethod
    def _is_standard_chunking_task(task_type: str) -> bool:
        task_type = (task_type or "").strip().lower()
        return task_type not in {"memory", "evaluation", "reembedding", "clone"} and not task_type.startswith("dataflow")

    @staticmethod
    def _validate_task_type(task_type: str) -> str:
        normalized = (task_type or "").strip().lower()
        if normalized in {"memory", "graphrag", "graph", "raptor", "mindmap", "wiki", "artifact", "skill", "structure_graph", "structure_mindmap", "timeline", "session_graph", "session_essence", "structure", "structuregraph", "structuremindmap", "sessiongraph", "sessionessence"}:
            raise ValueError(f"Unsupported task type: {normalized}")
        return normalized

    async def handle_task(self) -> None:
        # Reject retired work outside ingestion cleanup and cancellation paths.
        self._validate_task_type(self._task_context.task_type)
        try:
            await self.handle()
        except Exception:
            if self._is_standard_chunking_task(self._task_context.task_type):
                abort_doc_chunking_counter(self._task_context.doc_id, parse_run_id=self._task_context.parse_run_id)
            raise
        finally:
            task_id = self._task_context.id
            task_tenant_id = self._task_context.tenant_id
            task_dataset_id = self._task_context.kb_id
            task_doc_id = self._task_context.doc_id
            if self._task_context.has_canceled_func(task_id):
                if self._is_standard_chunking_task(self._task_context.task_type):
                    abort_doc_chunking_counter(task_doc_id, parse_run_id=self._task_context.parse_run_id)
                    try:
                        exists = await thread_pool_exec(
                            settings.docStoreConn.index_exist,
                            search.index_name(task_tenant_id),
                            task_dataset_id,
                        )
                        if exists:
                            ret = await thread_pool_exec(
                                settings.docStoreConn.delete,
                                {"doc_id": task_doc_id},
                                search.index_name(task_tenant_id),
                                task_dataset_id,
                            )
                            self._task_context.recording_context.save_func_return_value("docStoreConn.delete", ret)
                    except Exception as e:
                        logging.exception(f"Remove doc({task_doc_id}) from docStore failed when task({task_id}) canceled, exception: {e}")

    @timeout(60 * 60 * 3, 1)
    async def handle(self) -> None:
        """Handle a document processing task."""
        ctx = self._task_context
        task_type = self._validate_task_type(ctx.task_type)
        task_id = ctx.id

        # Check if task is canceled
        if ctx.has_canceled_func(task_id):
            ctx.progress_cb(-1, msg="Task has been canceled.")
            return

        # Generationless ingestion stages a new chunk set beside the protected
        # active set and performs its own expected-pointer CAS in the activator.
        # The normal task path still rejects direct mutation of published evidence.
        if (
            ctx.doc_id
            and ctx.doc_id not in {CANVAS_DEBUG_DOC_ID}
            and not getattr(ctx, "_docmind_defer_activation", False)
        ):
            await thread_pool_exec(
                DocumentService.assert_docmind_evidence_mutable,
                ctx.doc_id,
                f"TASK_EXECUTOR_{task_type.upper() or 'PARSE'}",
            )

        # Language defaults to "Chinese" via TaskContext._DEFAULTS 鈥?safe to bind model directly.
        # Bind embedding model (matching original do_handle_task order: bind + init_kb before routing)
        result = await self._bind_embedding_model()
        if result is None:
            return
        embedding_model, vector_size = result

        with embedding_model:
            self._init_kb(vector_size)

            # Handle dataflow tasks (after init_kb, matching original behavior)
            if task_type == "dataflow" and ctx.doc_id == CANVAS_DEBUG_DOC_ID:
                await self._run_dataflow()
                return

            if task_type.startswith("dataflow"):
                await self._run_dataflow()
                return

            if task_type == "evaluation":
                await self._run_evaluation()
            elif task_type == "reembedding":
                await self._run_reembedding()
            elif task_type == "clone":
                await self._run_clone()
            else:
                await self._run_standard_chunking(embedding_model, vector_size)

    def _init_kb(self, vector_size: int) -> None:
        """Initialize knowledge base index."""
        ctx = self._task_context
        idxnm = search.index_name(ctx.tenant_id)
        parser_id = ctx.parser_id
        # Create index if not exists
        settings.docStoreConn.create_idx(idxnm, ctx.kb_id, vector_size, parser_id)

    async def _run_dataflow(self) -> None:
        """Run dataflow pipeline."""
        dataflow_service = DataflowService(
            ctx=self._task_context,
            billing_hook=self._billing_hook,
        )
        await dataflow_service.run_dataflow()

    async def _run_evaluation(self) -> None:
        """Run evaluation task."""
        ctx = self._task_context
        ctx.progress_cb(1, "Evaluation task placeholder")

    async def _run_reembedding(self) -> None:
        """Run reembedding task."""
        ctx = self._task_context
        ctx.progress_cb(1, "Reembedding task placeholder")

    async def _run_clone(self) -> None:
        """Run clone task."""
        ctx = self._task_context
        ctx.progress_cb(1, "Clone task placeholder")

    async def _bind_embedding_model(self) -> Optional[tuple]:
        """Bind embedding model to task.

        Returns:
            Tuple of (embedding_model, vector_size) on success, or None on failure.
        """
        ctx = self._task_context
        task_tenant_id = ctx.tenant_id
        task_embedding_id = ctx.embd_id
        task_language = ctx.language

        try:
            if ctx.tenant_embd_id:
                try:
                    embd_model_config = get_model_config_by_id(task_tenant_id, LLMType.EMBEDDING, ctx.tenant_embd_id)
                except LookupError:
                    embd_model_config = resolve_model_config(task_tenant_id, LLMType.EMBEDDING, task_embedding_id)
            elif task_embedding_id:
                embd_model_config = resolve_model_config(task_tenant_id, LLMType.EMBEDDING, task_embedding_id)
            else:
                embd_model_config = get_tenant_default_model_by_type(task_tenant_id, LLMType.EMBEDDING)
            embedding_model = LLMBundle(task_tenant_id, embd_model_config, lang=task_language)
            vts, _ = embedding_model.encode(["ok"])
            return embedding_model, len(vts[0])
        except Exception as e:
            error_message = f"Fail to bind embedding model: {str(e)}"
            ctx.progress_cb(-1, msg=error_message)
            logging.exception(error_message)
            raise


    async def _run_standard_chunking(
        self,
        embedding_model: LLMBundle,
        vector_size: int,
    ) -> None:
        ctx = self._task_context
        try:
            await self._run_standard_chunking_impl(embedding_model, vector_size)
        except Exception as error:
            if ctx.parse_run_id:
                from api.db.services.parser_run_service import ParserRunService

                ParserRunService.fail_run(
                    ctx.parse_run_id,
                    error_code=getattr(error, "code", "PARSER_NORMALIZATION_FAILED"),
                    error_message=str(error),
                    retryable=getattr(error, "retryable", True),
                )
            abort_doc_chunking_counter(ctx.doc_id, parse_run_id=ctx.parse_run_id)
            raise

    async def _run_standard_chunking_impl(
        self,
        embedding_model: LLMBundle,
        vector_size: int,
    ) -> None:
        """Run standard chunking pipeline."""
        ctx = self._task_context
        task_id = ctx.id
        task_tenant_id = ctx.tenant_id
        task_dataset_id = ctx.kb_id
        task_doc_id = ctx.doc_id
        task_start_ts = timer()

        def on_chunking_start(wait_time):
            nonlocal task_start_ts
            task_start_ts += wait_time

        doc_task_llm_id = ctx.parser_config.get("llm_id") or ctx.llm_id
        ctx.raw_task["llm_id"] = doc_task_llm_id

        # Build chunks
        start_ts = timer()
        chunk_service = ChunkService(ctx=ctx)

        # Cloud-source ingestion supplies a job-scoped plaintext workspace and
        # must never fall back to the durable object store.
        ephemeral_workspace = getattr(ctx, "_docmind_ephemeral_workspace", None)

        def cloud_stage(name: str):
            return (
                stage_scope(getattr(settings, "STORAGE_IMPL", None), name, ctx.parse_run_id)
                if ephemeral_workspace is not None and ctx.parse_run_id
                else nullcontext()
            )

        if ephemeral_workspace is not None:
            binary = await asyncio.to_thread(ephemeral_workspace.input_path.read_bytes)
        else:
            bucket, name = File2DocumentService.get_storage_address(doc_id=ctx.doc_id)
            binary = await self._get_storage_binary(bucket, name)
        if binary is None:
            raise FileNotFoundError(f"Can not find file <{ctx.name}> from minio. Could you try it again.")

        chunks = await chunk_service.build_chunks(binary, on_chunking_start)
        ctx.recording_context.record("chunks", chunks)
        chunk_ids = [c.get("id") for c in chunks if isinstance(c, dict) and "id" in c]
        ctx.recording_context.record("chunk_ids_count", len(chunk_ids))

        logging.info("Build document {}: {:.2f}s".format(ctx.name, timer() - start_ts))

        if not chunks:
            if ctx.parse_run_id:
                if getattr(ctx, "_docmind_defer_activation", False):
                    from api.db.services.parser_run_service import ParserRunService

                    parsed_document = getattr(ctx, "_parser_platform_document", None)
                    if parsed_document is None:
                        raise RuntimeError("parser-platform document evidence is missing")
                    ParserRunService.update_lifecycle(ctx.parse_run_id, "CHUNKING_STAGING")
                    ParserRunService.mark_staging_validating(
                        parse_run_id=ctx.parse_run_id,
                        staged_chunk_count=0,
                        staged_token_count=0,
                        raw_artifact_ref=parsed_document.raw_artifact_ref,
                        warnings=list(parsed_document.warnings),
                    )
                    ParserRunService.update_lifecycle(ctx.parse_run_id, "ACTIVATING")
                    ctx._docmind_staged_result = {
                        "parser_run_id": ctx.parse_run_id,
                        "chunk_set_id": ctx.chunk_set_id,
                        "expected_chunk_count": 0,
                        "indexed_chunk_count": 0,
                        "provenance_complete": True,
                        "target_lifecycle": (
                            "READY_WITH_WARNING"
                            if parsed_document.status.value == "READY_WITH_WARNING"
                            else "READY"
                        ),
                    }
                    return
                raise RuntimeError("parser platform produced no searchable chunks")
            ctx.progress_cb(msg=f"No chunk built from {ctx.name}")
            if not await self._run_document_post_chunking_if_last(
                embedding_model,
                vector_size,
                task_start_ts,
                0,
                0,
            ):
                return
            task_time_cost = timer() - task_start_ts
            ctx.recording_context.record("task_status", "completed")
            ctx.progress_cb(prog=1.0, msg="Task done ({:.2f}s)".format(task_time_cost))
            return

        ctx.progress_cb(msg="Generate {} chunks".format(len(chunks)))

        if ctx.parse_run_id:
            from api.db.services.parser_run_service import ParserRunService

            ParserRunService.update_lifecycle(ctx.parse_run_id, "CHUNKING_STAGING")

        # Embed chunks
        start_ts = timer()
        embedding_service = EmbeddingService(ctx=ctx)
        try:
            with cloud_stage("embedding"):
                token_count, vector_size = await embedding_service.embed_chunks(chunks, embedding_model, ctx.parser_config)
        except TaskCanceledException:
            raise
        except Exception as e:
            error_message = "Generate embedding error:{}".format(str(e))
            ctx.progress_cb(-1, error_message)
            logging.exception(error_message)
            raise

        ctx.recording_context.record("token_count", token_count)
        ctx.recording_context.record("vector_size", vector_size)
        progress_message = "Embedding chunks ({:.2f}s)".format(timer() - start_ts)
        logging.info(progress_message)
        ctx.progress_cb(msg=progress_message)

        toc_thread = None
        if not ctx.parse_run_id and ctx.parser_id.lower() == "naive" and ctx.parser_config.get("toc_extraction", False):
            toc_thread = asyncio.create_task(asyncio.to_thread(self._build_toc, ctx, chunks, ctx.progress_cb))

        # Insert chunks
        chunk_count = len(set([chunk["id"] for chunk in chunks]))
        start_ts = timer()

        chunk_service = ChunkService(ctx=ctx)

        if ctx.has_canceled_func(task_id):
            if ctx.parse_run_id:
                raise TaskCanceledException("Parser-platform task was canceled before staging write")
            abort_doc_chunking_counter(task_doc_id)
            ctx.progress_cb(-1, msg="Task has been canceled.")
            return

        with cloud_stage("index_staging"):
            insert_result = await chunk_service.insert_chunks(task_id, task_tenant_id, task_dataset_id, chunks)
            if not insert_result:
                ctx.recording_context.record("insertion_result", "failed")
                if ctx.parse_run_id:
                    raise RuntimeError("parser-platform staging chunk insertion failed")
                abort_doc_chunking_counter(task_doc_id)
                return
        ctx.recording_context.record("insertion_result", "success")

        # Post-processing
        post_processor = PostProcessor(ctx=ctx)
        await post_processor.process_table_parser_metadata(task_doc_id, chunks)

        ctx.progress_cb(msg="Indexing done ({:.2f}s).".format(timer() - start_ts))

        toc_chunk = await self._process_toc_thread(toc_thread)
        if toc_chunk:
            ctx.recording_context.record("toc_chunk", [toc_chunk])
            await post_processor.insert_toc_chunk(toc_chunk, chunk_service)

        if ctx.has_canceled_func(task_id):
            if ctx.parse_run_id:
                raise TaskCanceledException("Parser-platform task was canceled before activation")
            abort_doc_chunking_counter(task_doc_id)
            ctx.progress_cb(-1, msg="Task has been canceled.")
            return

        # Update document stats
        if ctx.parse_run_id:
            from api.db.services.chunk_set_activation_service import (
                ACTIVE_SCOPE_CACHE,
                DocStoreChunkSetArtifactCleaner,
                PeeweeAtomicChunkSetStore,
            )
            from api.db.services.parser_run_service import ParserRunService
            from rag.parser_platform import (
                ChunkSetActivationCoordinator,
                ChunkSetFinalizationRequest,
                ParserArtifactRepository,
                ParserPlatformConfig,
            )

            parsed_document = getattr(ctx, "_parser_platform_document", None)
            if parsed_document is None:
                raise RuntimeError("parser-platform document evidence is missing")
            parser_run = ParserRunService.mark_staging_validating(
                parse_run_id=ctx.parse_run_id,
                staged_chunk_count=chunk_count,
                staged_token_count=token_count,
                raw_artifact_ref=parsed_document.raw_artifact_ref,
                warnings=list(parsed_document.warnings),
            )
            ParserRunService.update_lifecycle(ctx.parse_run_id, "ACTIVATING")
            if getattr(ctx, "_docmind_defer_activation", False):
                provenance_complete = all(
                    chunk.get("metadata", {}).get("parser_platform", {}).get("contributing_provenance")
                    for chunk in chunks
                )
                if chunks and not provenance_complete:
                    raise RuntimeError("parser-platform provenance incomplete")
                ctx._docmind_staged_result = {
                    "parser_run_id": ctx.parse_run_id,
                    "chunk_set_id": ctx.chunk_set_id,
                    "expected_chunk_count": chunk_count,
                    "indexed_chunk_count": chunk_count,
                    "provenance_complete": provenance_complete,
                    "target_lifecycle": (
                        "READY_WITH_WARNING"
                        if parsed_document.status.value == "READY_WITH_WARNING"
                        else "READY"
                    ),
                }
                return
            ok, document = DocumentService.get_by_id(task_doc_id)
            if not ok or document is None:
                raise RuntimeError("document disappeared before chunk-set activation")
            artifacts = ParserArtifactRepository(ParserPlatformConfig.from_env().artifact_root)
            coordinator = ChunkSetActivationCoordinator(
                PeeweeAtomicChunkSetStore(
                    artifact_cleaner=DocStoreChunkSetArtifactCleaner(
                        raw_artifact_cleaner=artifacts.remove_run_ref,
                        page_artifact_cleaner=artifacts.remove_page_artifacts,
                    )
                ),
                cache=ACTIVE_SCOPE_CACHE,
            )
            activation = coordinator.activate(
                ChunkSetFinalizationRequest(
                    document_id=task_doc_id,
                    kb_id=task_dataset_id,
                    parse_run_id=ctx.parse_run_id,
                    chunk_set_id=ctx.chunk_set_id,
                    expected_current_chunk_set_id=document.active_chunk_set_id,
                    expected_task_count=parser_run.expected_task_count,
                    completed_task_count=parser_run.completed_task_count,
                    failed_task_count=parser_run.failed_task_count,
                    expected_chunk_count=chunk_count,
                    staged_chunk_count=parser_run.staged_chunk_count,
                    indexed_chunk_count=chunk_count,
                    staged_token_count=token_count,
                    raw_artifact_complete=bool(parsed_document.raw_artifact_ref),
                    normalized_document_valid=True,
                    provenance_complete=all(
                        chunk.get("metadata", {}).get("parser_platform", {}).get("contributing_provenance")
                        for chunk in chunks
                    ),
                    required_ocr_complete=True,
                    embedding_complete=True,
                    target_lifecycle=(
                        "READY_WITH_WARNING"
                        if parsed_document.status.value == "READY_WITH_WARNING"
                        else "READY"
                    ),
                )
            )
            ctx.recording_context.record("chunk_set_activation", activation.__dict__)
            try:
                cleanup = coordinator.cleanup_expired(
                    retained_before=datetime.now(UTC).replace(tzinfo=None),
                )
                ctx.recording_context.record("chunk_set_cleanup", cleanup.__dict__)
            except Exception:
                logging.exception("Parser-platform retention cleanup failed after activation")
        elif ctx.write_interceptor:
            ctx.write_interceptor.intercept("DocumentService.increment_chunk_num")
        else:
            DocumentService.increment_chunk_num(task_doc_id, task_dataset_id, token_count, chunk_count, 0)

        if not await self._run_document_post_chunking_if_last(
            embedding_model,
            vector_size,
            task_start_ts,
            len(chunks),
            token_count,
        ):
            return

        task_time_cost = timer() - task_start_ts
        ctx.recording_context.record("task_status", "completed")
        ctx.progress_cb(prog=1.0, msg="Task done ({:.2f}s)".format(task_time_cost))

        logging.info("Chunk doc({}), page({}-{}), chunks({}), token({}), elapsed:{:.2f}".format(ctx.name, ctx.from_page, ctx.to_page, len(chunks), token_count, task_time_cost))

    async def _run_document_post_chunking_if_last(
        self,
        embedding_model: LLMBundle,
        vector_size: int,
        task_start_ts: float,
        chunks_len: int,
        token_count: int,
    ) -> bool:
        """Thin delegator. The pipeline lives in
        ``rag.svr.task_executor_refactor.chunk_post_processor``.
        """
        from rag.svr.task_executor_refactor.chunk_post_processor import (
            run_document_post_chunking_if_last,
        )

        return await run_document_post_chunking_if_last(
            self,
            embedding_model,
            vector_size,
            task_start_ts,
            chunks_len,
            token_count,
        )

    async def _process_toc_thread(self, toc_thread):
        try:
            if toc_thread:
                return await toc_thread
            else:
                return None
        finally:
            if toc_thread is not None and not toc_thread.done():
                toc_thread.cancel()

    @classmethod
    async def _get_storage_binary(cls, bucket: str, name: str) -> bytes:
        from common import settings

        """Get binary from storage."""
        return await thread_pool_exec(settings.STORAGE_IMPL.get, bucket, name)


    @classmethod
    def _build_toc(cls, ctx: TaskContext, docs: List[Dict], progress_cb: Callable) -> Optional[Dict]:
        """Build table of contents."""
        progress_cb(msg="Start to generate table of content ...")
        chat_model_config = resolve_model_config(ctx.tenant_id, LLMType.CHAT, ctx.llm_id)
        with LLMBundle(ctx.tenant_id, chat_model_config, lang=ctx.language) as chat_mdl:
            docs = sorted(
                docs,
                key=lambda d: (
                    d.get("page_num_int", 0)[0] if isinstance(d.get("page_num_int", 0), list) else d.get("page_num_int", 0),
                    d.get("top_int", 0)[0] if isinstance(d.get("top_int", 0), list) else d.get("top_int", 0),
                ),
            )

            # NOTE: asyncio.run() creates a new event loop in the worker thread
            # (this method is called via asyncio.to_thread), which is the
            # intended pattern for bridging sync -> async in a thread context.
            toc: list[dict] = asyncio.run(run_toc_from_text([d["content_with_weight"] for d in docs], chat_mdl, progress_cb))
            logging.info("------------ T O C -------------\n" + json.dumps(toc, ensure_ascii=False, indent="  "))

            for ii, item in enumerate(toc):
                try:
                    chunk_val = item.pop("chunk_id", None)
                    if chunk_val is None or str(chunk_val).strip() == "":
                        logging.warning(f"Index {ii}: chunk_id is missing or empty. Skipping.")
                        continue
                    curr_idx = int(chunk_val or -1)
                    if curr_idx >= len(docs):
                        logging.error(f"Index {ii}: chunk_id {curr_idx} exceeds docs length {len(docs)}.")
                        continue
                    item["ids"] = [docs[curr_idx]["id"]]
                    if ii + 1 < len(toc):
                        next_chunk_val = toc[ii + 1].get("chunk_id", "")
                        if str(next_chunk_val).strip() != "":
                            next_idx = int(next_chunk_val)
                            for jj in range(curr_idx + 1, min(next_idx + 1, len(docs))):
                                item["ids"].append(docs[jj]["id"])
                        else:
                            logging.warning(f"Index {ii + 1}: next chunk_id is empty, range fill skipped.")
                except (ValueError, TypeError) as e:
                    logging.error(f"Index {ii}: Data conversion error - {e}")
                except Exception as e:
                    logging.exception(f"Index {ii}: Unexpected error - {e}")

            if toc:
                import copy

                d = copy.deepcopy(docs[-1])
                d["content_with_weight"] = json.dumps(toc, ensure_ascii=False)
                d["toc_kwd"] = "toc"
                d["available_int"] = 0
                d["page_num_int"] = [100000000]
                d["id"] = make_chunk_id("toc", d["doc_id"], chunk=d, chunk_order_int="toc", positions=d.get("page_num_int"))
                return d
            return None
