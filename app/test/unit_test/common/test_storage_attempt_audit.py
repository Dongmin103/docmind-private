import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from common.storage_attempt_audit import StorageAttemptAudit, job_stage_scope


class Storage:
    def get(self, bucket, key):
        return b"private content"

    def put(self, bucket, key, content):
        raise RuntimeError("storage unavailable")

    def health(self):
        return True


def test_records_attempts_before_storage_errors_without_arguments(tmp_path):
    audited = StorageAttemptAudit(Storage(), tmp_path)

    with audited.attempt("a" * 32, 3):
        assert audited.get("secret-bucket", "secret-key") == b"private content"
        with pytest.raises(RuntimeError, match="storage unavailable"):
            audited.put("secret-bucket", "secret-key", b"private content")
        assert audited.health() is True

    reports = list(tmp_path.glob("*.json"))
    assert len(reports) == 1
    raw = reports[0].read_text(encoding="utf-8")
    report = json.loads(raw)
    assert report["job_id"] == "a" * 32
    assert report["fencing_token"] == 3
    assert report["methods"] == {
        "get": 1, "put": 1, "delete": 0, "rm": 0, "obj_exist": 0, "health": 1
    }
    assert "secret" not in raw
    assert "private content" not in raw


def test_failed_job_still_records_method_attempts(tmp_path):
    audited = StorageAttemptAudit(Storage(), tmp_path)

    with pytest.raises(RuntimeError, match="storage unavailable"):
        with audited.attempt("b" * 32, 4):
            audited.put("secret-bucket", "secret-key", b"private content")

    report = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert report["methods"]["put"] == 1
    assert report["status"] == "error"


def test_stage_timer_records_failed_partial_duration(tmp_path):
    audited = StorageAttemptAudit(Storage(), tmp_path)

    with audited.attempt("c" * 32, 5):
        with audited.stage("parse_http", "d" * 32):
            pass
        with pytest.raises(RuntimeError, match="parser failed"):
            with audited.stage("normalize_artifacts", "d" * 32):
                raise RuntimeError("parser failed")

    report = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert [stage["name"] for stage in report["stages"]] == ["parse_http", "normalize_artifacts"]
    assert [stage["status"] for stage in report["stages"]] == ["ok", "error"]
    assert all(stage["duration_ns"] >= 0 for stage in report["stages"])
    assert all(stage["parse_run_id"] == "d" * 32 for stage in report["stages"])
    assert sum(stage["duration_ns"] for stage in report["stages"]) <= report["duration_ns"]
    assert report["started_utc"] <= report["finished_utc"]


def test_job_stage_timer_records_cleanup_without_parse_run(tmp_path):
    audited = StorageAttemptAudit(Storage(), tmp_path)
    with audited.attempt("e" * 32, 6):
        with job_stage_scope(audited, "container_cleanup", "e" * 32):
            pass
    report = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert report["stages"][0]["job_id"] == "e" * 32
    assert report["stages"][0]["name"] == "container_cleanup"
    assert report["stages"][0]["duration_ns"] >= 0


def test_parallel_documents_record_comparable_stage_intervals(tmp_path):
    audited = StorageAttemptAudit(Storage(), tmp_path)
    entered = threading.Barrier(2)
    leaving = threading.Barrier(2)

    def document(job_id, stage):
        with audited.attempt(job_id, 1):
            with audited.stage(stage, job_id):
                entered.wait(timeout=5)
                leaving.wait(timeout=5)

    with ThreadPoolExecutor(2) as executor:
        futures = [executor.submit(document, 'a' * 32, 'embedding'),
                   executor.submit(document, 'b' * 32, 'parse_http')]
        for future in futures:
            future.result(timeout=10)
    reports = [json.loads(path.read_text()) for path in tmp_path.glob('*.json')]
    assert len(reports) == 2
    for report in reports:
        assert report['process_id'] == os.getpid()
        assert report['clock'] == 'perf_counter_ns'
        stage = report['stages'][0]
        assert report['started_ns'] <= stage['started_ns'] <= stage['finished_ns'] <= report['finished_ns']
        assert stage['duration_ns'] == stage['finished_ns'] - stage['started_ns']
        assert report['duration_ns'] == report['finished_ns'] - report['started_ns']
    stages = [report['stages'][0] for report in reports]
    assert max(stage['started_ns'] for stage in stages) < min(stage['finished_ns'] for stage in stages)
