"""Execute real migration inventories/normalization without opening a database."""
import ast
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[3]


def _stage(name):
    path = ROOT / 'tools/scripts/mysql_migration.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    namespace = {'MigrationStage': object, 'logger': __import__('logging').getLogger(__name__)}
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


def _merge_stage(rows, dry_run=False, create_table_only=False):
    stage = _stage('ModelTypeMergeStage')()
    class Database:
        def __init__(self):
            self.sql = []
        def table_exists(self, name):
            return True
        def execute_sql(self, sql):
            self.sql.append(sql)
            return type('Cursor', (), {'fetchall': lambda _: rows})()
    stage.db = Database()
    stage.dry_run = dry_run
    stage.create_table_only = create_table_only
    stage.current_timestamp = lambda: 10
    stage.generate_uuid = lambda: 'new-id'
    return stage


def _row(kind, status='active'):
    return ('original-id', 'model', 'provider', 'instance', kind, status, '{}', 1000, None, 1000, None)


@pytest.mark.parametrize('dry_run', [False, True])
@pytest.mark.parametrize('create_table_only', [False, True])
@pytest.mark.parametrize('kind', ['asr', 'speech2text', 'tts', 'ocr', 'unknown', None])
@pytest.mark.parametrize('status', ['active', 'unsupported'])
def test_merge_rejects_historical_types_before_any_mutation(kind, status, dry_run, create_table_only):
    rows = [_row('embedding'), _row(kind, status)]
    original = list(rows)
    stage = _merge_stage(rows, dry_run, create_table_only)
    with pytest.raises(ValueError, match='Unsupported stored model type'):
        stage.execute()
    assert rows == original
    assert len(stage.db.sql) == 1
    assert stage.db.sql[0].startswith('SELECT ')


@pytest.mark.parametrize('dry_run', [False, True])
def test_merge_supported_types_preserves_bit_union_and_alias(dry_run):
    rows = [_row(kind) for kind in ['chat', 'embedding', 'vision', 'image2text', 'rerank']]
    stage = _merge_stage(rows, dry_run)
    count, tables = stage.execute()
    assert count == 1
    assert stage.db.sql[0].startswith('SELECT ')
    if dry_run:
        assert not any(sql.lstrip().startswith(('INSERT', 'DROP', 'ALTER')) for sql in stage.db.sql)
    else:
        inserted = next(sql for sql in stage.db.sql if sql.lstrip().startswith('INSERT'))
        assert ", 27, 'active'," in inserted
        assert stage.db.sql[-1] == 'ALTER TABLE tenant_model_merge_tmp RENAME TO tenant_model'


def test_factory_catalog_tags_match_retained_types():
    import json
    factories = json.loads((ROOT / 'conf/llm_factories.json').read_text(encoding='utf-8'))['factory_llm_infos']
    tags = {factory['name']: factory['tags'] for factory in factories}
    assert not any(tag in tags['OpenAI-API-Compatible'] for tag in ('SPEECH2TEXT', 'MODERATION', 'TTS', 'OCR'))
    assert tags['Jina'] == 'TEXT RE-RANK'
