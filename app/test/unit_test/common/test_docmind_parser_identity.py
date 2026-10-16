import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _handle():
    path = ROOT / 'rag/svr/task_executor_refactor/task_handler.py'
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'TaskHandler')
    method = next(n for n in owner.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'handle')
    method.decorator_list = []
    namespace = {'thread_pool_exec': lambda *_a: None}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['handle']


@pytest.mark.parametrize('run,chunk', [('', ''), ('run', ''), ('bad', 'bad'), (' '*32, '1'*32)])
def test_docmind_identity_fails_before_embedding_bind(run, chunk):
    calls = []
    async def bind():
        calls.append('model')
        return None
    context = SimpleNamespace(task_type='', id='task', parser_id='naive', parser_config={}, parse_run_id=run, chunk_set_id=chunk, _docmind_defer_activation=True, has_canceled_func=lambda *_a: False, doc_id='document')
    handler = SimpleNamespace(_task_context=context, _validate_task_type=lambda t: t, _bind_embedding_model=bind)
    with pytest.raises(ValueError, match='identity'):
        asyncio.run(_handle()(handler))
    assert calls == []


def test_standard_blank_task_type_does_not_require_docmind_ids():
    calls = []
    async def bind():
        calls.append('model')
        return None
    context = SimpleNamespace(task_type='', id='task', parser_id='naive', parser_config={}, parse_run_id='', chunk_set_id='', has_canceled_func=lambda *_a: False, doc_id='')
    handler = SimpleNamespace(_task_context=context, _validate_task_type=lambda t: t, _bind_embedding_model=bind)
    asyncio.run(_handle()(handler))
    assert calls == ['model']
