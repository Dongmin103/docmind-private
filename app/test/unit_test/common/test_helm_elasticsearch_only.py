"""Render the retained chart without contacting a Kubernetes cluster."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

CHART = Path(__file__).resolve().parents[3] / 'helm'
HELM = os.environ.get('HELM_BINARY') or shutil.which('helm')
pytestmark = pytest.mark.skipif(not HELM, reason='Helm binary required for actual chart rendering')


def _render(*args):
    return subprocess.run([HELM, 'template', 'docmind', str(CHART), '--namespace', 'diet-check', *args], capture_output=True, text=True)


def test_default_chart_renders_retained_services_and_es_environment():
    result = _render()
    assert result.returncode == 0, result.stderr
    assert 'DOC_ENGINE: "elasticsearch"' in result.stdout
    assert 'ES_HOST: docmind-ragflow-es.diet-check.svc' in result.stdout
    for component in ('elasticsearch', 'mysql', 'redis', 'minio', 'ragflow'):
        assert f'app.kubernetes.io/component: {component}' in result.stdout
    for retired in ('infinity', 'opensearch', 'INFINITY_HOST', 'OS_HOST', 'OS_PORT', 'OPENSEARCH_PASSWORD'):
        assert retired not in result.stdout


@pytest.mark.parametrize('engine', ['infinity', 'opensearch', 'unknown', ''])
def test_chart_rejects_unsupported_engine_values(engine):
    result = _render('--set-string', f'env.DOC_ENGINE={engine}')
    assert result.returncode != 0
    assert 'env.DOC_ENGINE must be elasticsearch' in result.stderr
    assert result.stdout == ''


def test_external_retained_dependencies_render_without_internal_workloads():
    result = _render('--set', 'mysql.enabled=false,minio.enabled=false,redis.enabled=false', '--set-string', 'env.MYSQL_HOST=synthetic-mysql,env.MINIO_HOST=synthetic-minio,env.REDIS_HOST=synthetic-redis')
    assert result.returncode == 0, result.stderr
    for component in ('mysql', 'redis', 'minio'):
        assert f'app.kubernetes.io/component: {component}' not in result.stdout
    assert 'MYSQL_HOST: "synthetic-mysql"' in result.stdout
    assert 'MINIO_HOST: "synthetic-minio"' in result.stdout
    assert 'REDIS_HOST: "synthetic-redis"' in result.stdout
    assert 'app.kubernetes.io/component: elasticsearch' in result.stdout
