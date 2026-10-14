"""Run the mixed inventory's actual source-order assertions without DB imports."""
import ast
from pathlib import Path
import re

import pytest

APP = Path(__file__).resolve().parents[5]
SOURCE = APP / 'test/unit_test/api/db/services/test_docmind_protected_evidence_guard.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf8'))
OWNER = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == 'test_guard_precedes_first_mutation_in_inventory_paths')
ROWS = ast.literal_eval(OWNER.decorator_list[0].args[1])


@pytest.mark.parametrize(('path', 'function', 'guard_token', 'side_effect'), ROWS)
def test_actual_mixed_inventory_guard_order(path, function, guard_token, side_effect):
    nodes = [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in {'_function_source', OWNER.name}]
    for node in nodes:
        node.decorator_list = []
    namespace = {'REPO': APP, 'ast': ast}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    namespace[OWNER.name](path, function, guard_token, side_effect)


def test_fixture_cardinality_and_required_fields_match_actual_mixed_test():
    source = (APP / 'test/fixtures/docmind/docmind-protected-evidence-mutation-inventory-v1.yaml').read_text(encoding='utf8')
    blocks = source.split('  - id: ')[1:]
    identifiers = [block.splitlines()[0] for block in blocks]
    test_ids = [re.search(r'    test_id: (\w+)', block).group(1) for block in blocks]
    assert len(identifiers) == len(set(identifiers)) == len(set(test_ids))
    test = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == 'test_inventory_has_unique_complete_rows_and_stable_contract')
    cardinality = next(n.test.comparators[0].value for n in ast.walk(test) if isinstance(n, ast.Assert) and isinstance(n.test, ast.Compare) and ast.unparse(n.test.left) == 'len(rows)')
    assert len(blocks) == cardinality == 37
    for block in blocks:
        for field in ['entrypoint', 'service_chain', 'mutated_resources', 'first_side_effect', 'guard_boundary', 'operation', 'test_id']:
            assert f'    {field}: ' in block
    assert 'queue_dataflow' not in source
    assert 'agent_api.' not in source
    assert 'coverage_notes:' in source
