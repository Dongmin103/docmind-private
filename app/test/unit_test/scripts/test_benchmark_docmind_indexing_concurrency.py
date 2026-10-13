import importlib.util
from pathlib import Path

import pytest


def module():
    path = Path(__file__).resolve().parents[3] / 'scripts/benchmark_docmind_indexing_concurrency.py'
    assert path.exists(), 'benchmark measurement implementation is missing'
    spec = importlib.util.spec_from_file_location('concurrency_benchmark', path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_failed_process_is_never_a_successful_measurement():
    m = module()
    r = m.summarize(1, 4.0, [{'elapsed': 1.0, 'gpu_percent': 100, 'vram_mib': 4000, 'power_w': 120}], {'documents': 10})
    assert r['valid'] is False
    assert r['exit_code'] == 1


def test_sample_window_and_missing_gpu_are_explicit():
    m = module()
    samples = [{'elapsed': 1.0, 'gpu_percent': 40, 'vram_mib': 4000, 'power_w': 100}, {'elapsed': 2.0, 'gpu_percent': 80, 'vram_mib': 5000, 'power_w': 200}, {'elapsed': 9.0, 'gpu_percent': 100, 'vram_mib': 8000, 'power_w': 250}]
    r = m.summarize(0, 3.0, samples, {})
    assert r['gpu_percent'] == {'mean': 60.0, 'max': 80}
    assert r['vram_mib']['max'] == 5000
    assert m.summarize(0, 3.0, [], {})['valid'] is False


def test_comparison_rejects_different_inputs_and_failed_runs():
    m = module()
    def run(seconds, manifest='a', valid=True):
        return {'valid': valid, 'elapsed_seconds': seconds, 'metadata': {'manifest_sha256': manifest, 'model': 'gte', 'batch': 32}}
    assert m.compare([run(10)]*3, [run(8)]*3)['improvement_percent'] == 20.0
    with pytest.raises(ValueError): m.compare([run(10)]*3, [run(8, 'b')]*3)
    with pytest.raises(ValueError): m.compare([run(10)]*3, [run(8, valid=False)]*3)

