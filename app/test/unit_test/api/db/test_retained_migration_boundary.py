"""Execute migration registration offline; never import models or open a database."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

APP = Path(__file__).resolve().parents[4]


def migration_events(database_type):
    tree = ast.parse((APP / "api/db/db_models.py").read_text(encoding="utf8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "migrate_db")
    events = []
    migrator = SimpleNamespace(drop_index=lambda table, name: ("drop_index", table, name))
    namespace = {
        "settings": SimpleNamespace(DATABASE_TYPE=database_type),
        "DatabaseMigrator": {database_type: SimpleNamespace(value=lambda db: migrator)},
        "DB": object(),
        "logging": SimpleNamespace(ERROR=40, NOTSET=0, disable=lambda level: events.append(("logging", level)), critical=lambda message: events.append(("critical", message))),
        "is_gaussdb_compatible_database": lambda: database_type == "GAUSSDB",
        "OperationalError": type("OperationalError", (Exception,), {}),
        "ProgrammingError": type("ProgrammingError", (Exception,), {}),
    }
    def record(name):
        def call(*args, **kwargs):
            if name.startswith("alter_db_"):
                events.append((name, *args[1:3]))
            elif name == "migrate":
                events.append(args[0])
            else:
                events.append((name,))
        return call
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and node.id.endswith("Field"):
            namespace[node.id] = lambda *args, **kwargs: object()
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id not in namespace:
            namespace[node.func.id] = record(node.func.id)
    exec(compile(ast.Module(body=[function], type_ignores=[]), "<offline-migrations>", "exec"), namespace)
    namespace["migrate_db"]()
    return events


REQUIRED_COLUMNS = {
    "document": {"content_hash", "requested_parse_run_id"},
    "tenant_llm": {"status"},
    "connector2kb": {"auto_parse"},
    "llm_factories": {"rank"},
    "api_4_conversation": {"name", "exp_user_id", "version_title"},
    "sync_logs": {"task_type"},
    "user_canvas_version": {"release"},
    "user_canvas": {"tags"},
    "tenant": {"ocr_id", "tenant_ocr_id"},
    "docmind_catalog_version": {"readiness_mode", "source_ready_version_id", "manual_saved_by", "manual_saved_at", "snapshot_schema_version", "source_tree_hash", "root_identity_sha256"},
    "docmind_project": {"source_root_file_id"},
    "docmind_source": {"default_folder_id"},
    "docmind_ingestion_job": {"retry_not_before", "pdf_ocr_requested", "observation_epoch", "observation_generation", "pdf_ocr_consented_by", "pdf_ocr_consented_at", "pdf_ocr_consent_key_hash"},
    "docmind_source_document": {"source_dirty", "observation_epoch", "observation_generation", "last_change_sequence", "last_change_received_at", "stable_observed_at", "latest_target_version_id", "source_object_id", "host_file_identity"},
    "docmind_source_scan": {"protocol_version", "scan_start_epoch", "scan_start_sequence"},
    "docmind_source_deletion": {"deletion_generation"},
    "docmind_source_version": {"search_cleanup_complete"},
    "docmind_source_scan_entry": {"host_file_identity"},
    "docmind_folder": {"source_file_id"},
    "docmind_folder_version": {"parent_folder_id", "source_file_id", "relative_path", "display_name", "ordinal", "depth"},
}


@pytest.mark.parametrize("database_type", ["MYSQL", "GAUSSDB"])
def test_retained_columns_types_and_cleanup_backfill_are_registered(database_type):
    events = migration_events(database_type)
    registered = {(event[1], event[2]) for event in events if event[0] == "alter_db_add_column"}
    required = {(table, column) for table, columns in REQUIRED_COLUMNS.items() for column in columns}
    assert required <= registered, sorted(required - registered)
    for table, column in [("tenant_llm", "api_key"), ("system_settings", "value"), ("document", "size"), ("file", "size"), ("chat_channel", "status")]:
        assert ("alter_db_column_type", table, column) in events
    assert ("alter_db_rename_column", "chat_channel", "dialog_id") in events
    assert events.index(("alter_db_add_column", "docmind_source_version", "search_cleanup_complete")) < events.index(("backfill_docmind_search_cleanup_complete",)) < events.index(("alter_db_add_column", "docmind_source_document", "source_object_id"))
    assert not [(table, column) for table, column in registered if table in {"compilation_template", "compilation_template_group", "file_commit", "file_commit_item"} or column.startswith(("graphrag_task_", "raptor_task_", "mindmap_task_", "wiki_task_", "skill_task_", "structure_task_", "structure_graph_task_", "structure_mindmap_task_", "timeline_task_", "session_graph_task_", "session_essence_task_"))]


@pytest.mark.parametrize("database_type", ["MYSQL", "GAUSSDB"])
def test_migration_finalization_restores_logging_and_model_call_order(database_type):
    events = migration_events(database_type)
    assert events[0] == ("logging", 40)
    assert events[-4:] == [("logging", 0), ("migrate_add_unique_email",), ("migrate_model_type_names",), ("ensure_model_indexes",)]
    relax = events.index(("relax_gaussdb_empty_string_compatible_columns",))
    schema_indexes = [index for index, event in enumerate(events) if event[0].startswith("alter_db_")]
    assert relax > max(schema_indexes)
    assert events[relax + 1:-4] == [
        ("drop_index", "tenant_model_instance", "idx_api_key_provider_id"),
        ("drop_index", "tenant_model_instance", "tenantmodelinstance_api_key_provider_id"),
        ("drop_index", "tenant_model", "idx_provider_model_instance"),
    ]
