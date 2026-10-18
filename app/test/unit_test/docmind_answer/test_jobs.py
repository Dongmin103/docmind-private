"""Queue contracts against isolated SQLite; no external services or source data."""
import asyncio
import importlib.util
import time
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def jobs(monkeypatch, tmp_path):
    from peewee import SqliteDatabase
    db = SqliteDatabase(tmp_path/'jobs.sqlite', pragmas={'journal_mode': 'wal'}, timeout=5)
    monkeypatch.setitem(sys.modules, 'api.db.db_models', types.SimpleNamespace(DB=db))
    path = Path(__file__).resolve().parents[3] / "api/apps/services/docmind_answer_jobs.py"
    spec = importlib.util.spec_from_file_location("jobs_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_crypt_key", lambda: b"s" * 32)
    monkeypatch.setattr(module, "catalog_fence", lambda *_: ["kb", "v1"])
    monkeypatch.setattr(module, "validate_result", lambda *_: None)
    module.initialize()
    return module, db


PAYLOAD = {"question": "synthetic", "scope": {"mode": "all"}, "neighbor_window": 2}
ID = "a" * 32


def test_duplicate_acceptance_lost_ack_and_ownership(jobs):
    j, c = jobs
    assert j.submit("tenant", "user", ID, PAYLOAD)["status"] == "queued"
    assert j.submit("tenant", "user", ID, PAYLOAD)["job_id"] == ID
    assert j.Job.select().where(j.Job.status.in_(['queued', 'running'])).count() == 1
    with pytest.raises(j.JobError, match="CONFLICT"):
        j.submit("tenant", "other", ID, PAYLOAD)
    with pytest.raises(j.JobError, match="CONFLICT"):
        j.submit("tenant", "user", ID, {**PAYLOAD, "question": "changed"})
    with pytest.raises(j.JobError, match="NOT_FOUND"):
        j.get("other-tenant", "user", ID)
    assert "synthetic" not in str(j.get_row(ID))


def test_global_claim_lease_recovery_and_stale_completion(jobs):
    j, c = jobs
    j.submit("t", "u", ID, PAYLOAD)
    key = j.claim("first")
    assert key and j.claim("second") is None
    assert j.renew(key, "first") == 1
    # Simulate process loss and lease expiry without a real thirty-second wait.
    j.Coordinator.update(lease_until=0).execute()
    j.Job.update(lease_until=0).where(j.Job.id == key).execute()
    assert j.claim("second") == key
    assert j.renew(key, "first") == 0
    assert j.finish(key, "first", result={"answer": "stale"}) == 0
    assert j.finish(key, "second", result={"answer": "verified"}) == 1
    assert j.get("t", "u", ID)["result"] == {"answer": "verified"}
    assert j.Job.select().where(j.Job.status.in_(['queued', 'running'])).count() == 0
    assert 3500 < j.get_row(key)["expires_at"] - time.time() <= 3600


def test_completed_result_revalidates_source_before_every_read(jobs, monkeypatch):
    j, _ = jobs
    j.submit("t", "u", ID, PAYLOAD)
    key = j.claim("lease")
    j.finish(key, "lease", result={"answer": "verified"})
    monkeypatch.setattr(j, "catalog_fence", lambda *_: ["kb", "v2"])
    with pytest.raises(j.JobError, match="SOURCE_CHANGED"):
        j.get("t", "u", ID)


def test_queue_capacity_and_expired_payload_cleanup(jobs, monkeypatch):
    j, c = jobs
    monkeypatch.setattr(j, "MAX_PENDING", 1)
    j.submit("t", "u", ID, PAYLOAD)
    with pytest.raises(j.JobError, match="QUEUE_FULL"):
        j.submit("t", "u", "b"*32, PAYLOAD)
    j.Job.delete().where(j.Job.id == ID).execute()
    assert j.claim("lease") is None
    assert j.Job.select().where(j.Job.status.in_(['queued', 'running'])).count() == 0


def test_worker_preserves_known_error_and_bounds_entire_job(jobs, monkeypatch):
    j, _ = jobs
    class AnswerError(RuntimeError):
        pass
    async def slow(*args, **kwargs):
        await asyncio.sleep(1)
    services = types.ModuleType("api.apps.services")
    services.docmind_answer_service = types.SimpleNamespace(AnswerError=AnswerError, answer=slow)
    monkeypatch.setitem(sys.modules, "api.apps.services", services)
    monkeypatch.setattr(j, "JOB_SECONDS", 0.02)
    j.submit("t", "u", ID, PAYLOAD)
    key = j.claim("lease")
    asyncio.run(j.run_one(key, "lease"))
    assert j.get("t", "u", ID)["error_code"] == "DOCMIND_LLM_TIMEOUT"


def test_worker_restart_replays_request_and_returns_result(jobs, monkeypatch):
    j, c = jobs
    calls = []
    async def answer(tenant, **payload):
        calls.append((tenant, payload))
        return {"answer": "verified"}
    services = types.ModuleType("api.apps.services")
    services.docmind_answer_service = types.SimpleNamespace(AnswerError=RuntimeError, answer=answer)
    monkeypatch.setitem(sys.modules, "api.apps.services", services)
    j.submit("t", "u", ID, PAYLOAD)
    key = j.claim("dead-process")
    j.Coordinator.update(lease_until=0).execute()
    j.Job.update(lease_until=0).where(j.Job.id == key).execute()
    j.claim("replacement")
    asyncio.run(j.run_one(key, "replacement"))
    assert calls == [("t", PAYLOAD)]
    assert j.get("t", "u", ID)["result"] == {"answer": "verified"}


def test_lost_lease_cancels_generation_without_publishing(jobs, monkeypatch):
    j, c = jobs
    cancelled = []
    async def slow(*args, **kwargs):
        try:
            await asyncio.sleep(100)
        finally:
            cancelled.append(True)
    async def lost(*args):
        await asyncio.sleep(0.01)
        raise j.JobError("DOCMIND_ANSWER_LEASE_LOST")
    services = types.ModuleType("api.apps.services")
    services.docmind_answer_service = types.SimpleNamespace(AnswerError=RuntimeError, answer=slow)
    monkeypatch.setitem(sys.modules, "api.apps.services", services)
    monkeypatch.setattr(j, "_heartbeat", lost)
    j.submit("t", "u", ID, PAYLOAD)
    key = j.claim("lease")
    with pytest.raises(j.JobError, match="LEASE_LOST"):
        asyncio.run(j.run_one(key, "lease"))
    assert cancelled == [True]
    assert j.get_row(key)["status"] == "running"
    assert not j.get_row(key)["result"]
