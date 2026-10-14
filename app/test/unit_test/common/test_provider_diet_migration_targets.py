"""Execute real migration inventories/normalization without opening a database."""
import ast
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[3]


def _stage(name):
    path = ROOT / 'tools/scripts/mysql_migration.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    namespace = {'MigrationStage': object}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


def test_model_id_rewrite_targets_only_retained_owner_fields():
    stage = _stage('ModelIdConfigStage')
    assert stage.source_tables == ['tenant', 'knowledgebase', 'document', 'dialog']
    assert stage.target_tables == stage.source_tables
    assert set(stage.string_columns) == {'tenant', 'knowledgebase', 'dialog'}
    assert set(stage.json_columns) == {'knowledgebase', 'document'}
    assert not {'asr_id', 'tts_id', 'ocr_id'} & stage.model_id_fields
    assert stage().normalize_model_id('gte@OpenAI-API-Compatible') == ('gte@default@OpenAI-API-Compatible', True)
    assert stage().normalize_model_id('gte@instance@OpenAI-API-Compatible') == ('gte@instance@OpenAI-API-Compatible', False)


def test_tenant_id_migration_has_no_retired_owner_or_type_targets():
    stage = _stage('TenantModelIdMigrationStage')
    assert stage.target_tables == ['tenant', 'knowledgebase', 'dialog']
    assert 'memory' not in stage.source_tables
    assert set(stage.TENANT_ID_FIELDS) == {'tenant', 'knowledgebase', 'dialog'}
    assert not {'asr', 'speech2text', 'tts', 'ocr'} & set(stage.MODEL_TYPE_TO_INT)
    assert stage.MODEL_TYPE_TO_INT['vision'] == 8
