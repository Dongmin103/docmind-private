# Task 6 backend long-term memory removal

Status: DONE_WITH_CONCERNS. No live services, DB imports/startup, writes, original documents, installs, volumes, or data migrations were used. Existing memory table is unmanaged; no drop or purge added.

## Changes

Removed all long-term memory Python API/service/storage owners and exclusive SDK exports/methods/tests. Removed Memory ORM declaration and compatibility-column registration, startup repair/cache/sequence calls, message-store initialization, account memory deletion block, constants/enums, TaskContext fields/accessors, interceptor method, agent message saving and memory retrieval. Dataset retrieval and other agent primitives remain for their owner. Conversation/dialog owners and DocMind answer/history were untouched. No memory-only build declarations or explicit dependencies found in app/pyproject.toml or SDK pyproject. General provider/graph dependencies untouched.

TaskHandler.handle and handle_task both reject strip/lower-normalized memory with ValueError before cancellation/model/evidence/index operations. Wrapper rejection is outside try/finally, so rejected work cannot abort counters or delete document chunks, even when canceled. Standard blank type is retained and tested through actual handler routing. Other currently accepted task types remain accepted as requested by parent. Normalization is shared with cleanup classification.

## RED/GREEN evidence

New offline boundary executes actual AST-loaded handler methods with DB/model import boundaries excluded (not source-string assertions). Initial RED command `python app/test/unit_test/rag/svr/task_executor_refactor/test_retired_memory_boundary.py` failed: retired work reached old RecordingContext/evidence paths and mixed-case work invoked abort counter. Initial harness path/stub setup errors were corrected before assessing behavior. GREEN final direct command: 4 tests OK; 12 case/cancellation/entrypoint combinations. Includes blank standard route and cold init_web_data actual function with retained action doubles and no memory repair globals.

With PYTHONPATH=<worktree>/app and Python `C:/DocMindDev/docmind/.local/pptx-native-trial-venv/Scripts/python.exe`:

- `-m pytest -c NUL -p no:cacheprovider --noconftest app/test/unit_test/rag/svr/task_executor_refactor/test_task_context.py app/test/unit_test/rag/svr/task_executor_refactor/test_write_operation_interceptor.py app/test/unit_test/rag/svr/task_executor_refactor/test_retired_memory_boundary.py -q`: 80 passed, 12 subtests passed. --noconftest avoids unrelated numpy bootstrap; retained classes import normally.
- `-m pytest -c NUL -p no:cacheprovider --confcutdir=app/test/unit_test/common app/test/unit_test/common/test_lazy_backend_imports.py -q`: 27 passed. Actual settings init imports selected document backend/storage only; no message store exists.
- Same command targeting `app/test/unit_test/common/test_gaussdb_settings.py`: 6 passed.
- `-m pytest -c NUL -p no:cacheprovider --confcutdir=app/test/unit_test/api/apps/services app/test/unit_test/api/apps/services/test_docmind_ingestion_runtime.py -q`: 1 passed, 7 setup errors because numpy unavailable in host test venv, from common/doc_store/doc_store_base.py. No dependency install attempted.
- Ruff F/E9 on touched handler/context/interceptor, agent message/retrieval and new boundary: all checks passed. Broad existing-policy ruff produced 111 issues mostly preexisting style/security rules; not claimed green. New unused RecordingContext import removed. Syntax AST parsed all 23 changed existing Python modules. git diff --check passes. Existing file line endings preserved individually.

## Consumer review and limits

Production search api/common/rag/agent/sdk for imports of memory, memory_service, memory_message_service, MemoryType/MemoryStorageType, msgStoreConn and memory constants found no consumers (only ordinary prose phrase from memory). API discovery loads *_api.py; exclusive memory_api.py deletion removes /api/v1/memories and long-term /messages ownership. Cold all-blueprint discovery and fake ORM/account full behavior tests were not executed on this constrained host; source owners were inspected and account deletion retained tenant/document protection/history steps. Remaining shared GaussDB memory fixtures adjusted without broad rewrite; their full suites were not run. Retained history owners untouched but full history/citation behavior not re-executed in this slice.

Actual retained caller: docmind_ingestion_runtime.py builds blank task_type and awaits TaskHandler(context).handle_task via asyncio.wait_for/asyncio.run. Explicit rejection propagates out of execute before staging result acceptance; no silent success/fallback. No generic queue consumer exists in inspected checkout. No terminal queue ACK claim is made; deployment consumer verification remains required if a separate worker exists.

Frontend/Go untouched. Separate agent/graph/alternate runtime removals and dependencies remain parent-owned. No live data action is part of this commit.

## Files

- app/agent/component/message.py
- app/agent/tools/retrieval.py
- app/api/apps/restful_apis/memory_api.py
- app/api/apps/services/memory_api_service.py
- app/api/constants.py
- app/api/db/db_models.py
- app/api/db/init_data.py
- app/api/db/joint_services/memory_message_service.py
- app/api/db/joint_services/user_account_service.py
- app/api/db/services/memory_service.py
- app/api/utils/memory_utils.py
- app/common/constants.py
- app/common/settings.py
- app/memory/__init__.py
- app/memory/services/__init__.py
- app/memory/services/messages.py
- app/memory/services/query.py
- app/memory/utils/__init__.py
- app/memory/utils/aggregation_utils.py
- app/memory/utils/es_conn.py
- app/memory/utils/gaussdb_conn.py
- app/memory/utils/highlight_utils.py
- app/memory/utils/infinity_conn.py
- app/memory/utils/msg_util.py
- app/memory/utils/ob_conn.py
- app/memory/utils/prompt_util.py
- app/rag/prompts/generator.py
- app/rag/svr/task_executor_refactor/task_context.py
- app/rag/svr/task_executor_refactor/task_handler.py
- app/rag/svr/task_executor_refactor/write_operation_interceptor.py
- app/sdk/python/ragflow_sdk/__init__.py
- app/sdk/python/ragflow_sdk/modules/memory.py
- app/sdk/python/ragflow_sdk/ragflow.py
- app/test/testcases/restful_api/test_memories_messages.py
- app/test/testcases/restful_api/test_memory_messages.py
- app/test/testcases/restful_api/test_message_routes_unit.py
- app/test/testcases/test_sdk_api/test_memory_management/conftest.py
- app/test/testcases/test_sdk_api/test_memory_management/test_create_memory.py
- app/test/testcases/test_sdk_api/test_memory_management/test_list_memory.py
- app/test/testcases/test_sdk_api/test_memory_management/test_rm_memory.py
- app/test/testcases/test_sdk_api/test_memory_management/test_update_memory.py
- app/test/testcases/test_sdk_api/test_message_management/conftest.py
- app/test/testcases/test_sdk_api/test_message_management/test_add_message.py
- app/test/testcases/test_sdk_api/test_message_management/test_forget_message.py
- app/test/testcases/test_sdk_api/test_message_management/test_get_message_content.py
- app/test/testcases/test_sdk_api/test_message_management/test_get_recent_message.py
- app/test/testcases/test_sdk_api/test_message_management/test_list_message.py
- app/test/testcases/test_sdk_api/test_message_management/test_search_message.py
- app/test/testcases/test_sdk_api/test_message_management/test_update_message_status.py
- app/test/testcases/test_web_api/test_memory_app/conftest.py
- app/test/testcases/test_web_api/test_memory_app/test_create_memory.py
- app/test/testcases/test_web_api/test_memory_app/test_list_memory.py
- app/test/testcases/test_web_api/test_memory_app/test_rm_memory.py
- app/test/testcases/test_web_api/test_memory_app/test_update_memory.py
- app/test/testcases/test_web_api/test_message_app/conftest.py
- app/test/testcases/test_web_api/test_message_app/test_add_message.py
- app/test/testcases/test_web_api/test_message_app/test_forget_message.py
- app/test/testcases/test_web_api/test_message_app/test_get_message_content.py
- app/test/testcases/test_web_api/test_message_app/test_get_recent_message.py
- app/test/testcases/test_web_api/test_message_app/test_list_message.py
- app/test/testcases/test_web_api/test_message_app/test_message_routes_unit.py
- app/test/testcases/test_web_api/test_message_app/test_search_message.py
- app/test/testcases/test_web_api/test_message_app/test_update_message_status.py
- app/test/unit_test/api/apps/restful_apis/test_add_message_user_id.py
- app/test/unit_test/api/db/gaussdb_test_utils.py
- app/test/unit_test/api/db/joint_services/test_memory_message_service.py
- app/test/unit_test/api/db/joint_services/test_memory_message_timestamp_normalization.py
- app/test/unit_test/api/db/services/test_gaussdb_dialog_sql.py
- app/test/unit_test/api/db/services/test_gaussdb_doc_metadata_service.py
- app/test/unit_test/api/db/test_gaussdb_user_flow.py
- app/test/unit_test/common/test_gaussdb_settings.py
- app/test/unit_test/common/test_lazy_backend_imports.py
- app/test/unit_test/memory/utils/test_gaussdb_memory_conn.py
- app/test/unit_test/memory/utils/test_ob_conn_aggregation.py
- app/test/unit_test/memory/utils/test_ob_conn_highlight.py
- app/test/unit_test/memory/utils/test_prompt_util.py
- app/test/unit_test/rag/nlp/test_gaussdb_retrieval.py
- app/test/unit_test/rag/svr/task_executor_refactor/test_task_context.py
- app/test/unit_test/rag/svr/task_executor_refactor/test_write_operation_interceptor.py
- app/test/unit_test/rag/svr/task_executor_refactor/test_retired_memory_boundary.py
