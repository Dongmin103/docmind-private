"""Database-backed answer queue with fenced leases and encrypted, expiring payloads."""

import asyncio
import base64
import hashlib
import json
import logging
import re
import secrets
import time
from contextlib import contextmanager

from peewee import CharField, DoubleField, IntegerField, Model, SqliteDatabase, TextField
from api.db.db_models import DB

from Cryptodome.Cipher import AES

RETENTION_SECONDS = 3600
LEASE_SECONDS = 30
JOB_SECONDS = 720
QUEUE_SECONDS = 1800
MAX_PENDING = 16
logger = logging.getLogger(__name__)


class LongText(TextField):
    field_type = 'LONGTEXT'


class Job(Model):
    id = CharField(max_length=32, primary_key=True)
    owner = CharField(max_length=64)
    digest = CharField(max_length=64)
    data = LongText()
    result = LongText(null=True)
    status = CharField(max_length=16, index=True)
    created_at = DoubleField()
    expires_at = DoubleField(index=True)
    attempts = IntegerField(default=0)
    token = CharField(max_length=64, default='')
    lease_until = DoubleField(default=0)
    error_code = CharField(max_length=128, default='')

    class Meta:
        database = DB
        table_name = 'docmind_answer_job'


class Coordinator(Model):
    id = IntegerField(primary_key=True)
    token = CharField(max_length=64, default='')
    lease_until = DoubleField(default=0)

    class Meta:
        database = DB
        table_name = 'docmind_answer_queue'


class JobError(RuntimeError):
    pass


def initialize():
    with DB.connection_context():
        DB.create_tables([Job, Coordinator], safe=True)
        Coordinator.get_or_create(id=1)


@contextmanager
def _locked():
    with DB.connection_context():
        # SQLite tests use an immediate transaction; MySQL serializes on one row.
        with (DB.atomic('IMMEDIATE') if isinstance(DB, SqliteDatabase) else DB.atomic()):
            query = Coordinator.select().where(Coordinator.id == 1)
            coordinator = (query if isinstance(DB, SqliteDatabase) else query.for_update()).get()
            yield coordinator


def get_row(job_id):
    with DB.connection_context():
        row = Job.get_or_none(Job.id == job_id, Job.expires_at > time.time())
        return dict(row.__data__) if row else None


def _crypt_key():
    from common import settings

    return hashlib.sha256((str(settings.get_secret_key()) + ":answer-jobs:v1").encode()).digest()


def _seal(value):
    cipher = AES.new(_crypt_key(), AES.MODE_GCM)
    body, tag = cipher.encrypt_and_digest(json.dumps(value, ensure_ascii=False).encode())
    return base64.b64encode(cipher.nonce + tag + body).decode()


def _open(value):
    body = base64.b64decode(value)
    return json.loads(AES.new(_crypt_key(), AES.MODE_GCM, nonce=body[:16]).decrypt_and_verify(body[32:], body[16:32]))


def _key(job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise JobError("DOCMIND_ANSWER_JOB_NOT_FOUND")
    return job_id


def _owner(tenant_id, user_id):
    return hashlib.sha256(json.dumps([tenant_id, user_id]).encode()).hexdigest()


def catalog_fence(tenant_id, payload):
    from api.apps.services import docmind_api_service as search
    from api.db.db_models import DB

    try:
        with DB.connection_context():
            catalog = search._load_serving_catalog(tenant_id)
            if not search.KnowledgebaseService.accessible(catalog.dataset_id, tenant_id):
                raise JobError("DOCMIND_CONTEXT_SCOPE_INVALID")
            if payload.get("project_id") is not None and payload["project_id"] != catalog.dataset_id:
                raise ValueError("DOCMIND_INVALID_PROJECT")
            search._resolve_scope(catalog, payload["scope"])
            return [catalog.dataset_id, search._catalog_version_id(catalog)]
    except (ValueError, PermissionError, search.DocmindCatalogNotInitializedError):
        raise JobError("DOCMIND_SOURCE_CHANGED") from None


def validate_result(tenant_id, payload, result):
    from api.apps.services import docmind_answer_service as answers
    from api.apps.services import docmind_api_service as search
    from api.db.db_models import DB

    with DB.connection_context():
        catalog = search._load_serving_catalog(tenant_id)
        allowed = set(search._resolve_scope(catalog, payload["scope"]).document_ids)
        if result["dataset_id"] != catalog.dataset_id:
            raise JobError("DOCMIND_SOURCE_CHANGED")
        for citation in result["citations"]:
            if citation["doc_id"] not in allowed:
                raise JobError("DOCMIND_SOURCE_CHANGED")
            rows = answers._query_chunks(tenant_id, catalog.dataset_id, citation["doc_id"], ids=[citation["chunk_id"]])
            if len(rows) != 1 or rows[0].get("content_with_weight") != citation["content"] or rows[0].get("chunk_set_id") != citation.get("chunk_set_id"):
                raise JobError("DOCMIND_SOURCE_CHANGED")


def submit(tenant_id, user_id, job_id, payload):
    _key(job_id)
    owner = _owner(tenant_id, user_id)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    existing = get_row(job_id)
    if existing:
        if existing['owner'] != owner or existing['digest'] != digest:
            raise JobError('DOCMIND_ANSWER_JOB_CONFLICT')
        return public(existing, job_id)
    fence = catalog_fence(tenant_id, payload)
    data = _seal({'tenant_id': tenant_id, 'payload': payload, 'fence': fence})
    with _locked():
        now = time.time()
        Job.delete().where(Job.expires_at <= now).execute()
        existing = Job.get_or_none(Job.id == job_id)
        if existing:
            if existing.owner != owner or existing.digest != digest:
                raise JobError('DOCMIND_ANSWER_JOB_CONFLICT')
            return public(existing.__data__, job_id)
        if Job.select().where(Job.status.in_(['queued', 'running'])).count() >= MAX_PENDING:
            raise JobError('DOCMIND_ANSWER_QUEUE_FULL')
        row = Job.create(id=job_id, owner=owner, digest=digest, data=data,
                         status='queued', created_at=now, expires_at=now+RETENTION_SECONDS)
        return public(row.__data__, job_id)


def public(row, job_id):
    return {"job_id": job_id, "status": row["status"], "created_at": float(row["created_at"]),
            "poll_after_ms": 2000, "error_code": row.get("error_code") or None}


def get(tenant_id, user_id, job_id):
    row = get_row(_key(job_id))
    if not row or row.get("owner") != _owner(tenant_id, user_id):
        raise JobError("DOCMIND_ANSWER_JOB_NOT_FOUND")
    out = public(row, job_id)
    if row["status"] == "completed":
        data = _open(row["data"])
        # Revalidate authority and source generation on every result retrieval.
        if catalog_fence(tenant_id, data["payload"]) != data["fence"]:
            raise JobError("DOCMIND_SOURCE_CHANGED")
        result = _open(row["result"])
        try:
            validate_result(tenant_id, data["payload"], result)
        except Exception:
            raise JobError("DOCMIND_SOURCE_CHANGED") from None
        out["result"] = result
    return out


def claim(token):
    with _locked() as coordinator:
        now = time.time()
        Job.delete().where(Job.expires_at <= now).execute()
        if coordinator.lease_until > now:
            return None
        row = (Job.select().where(Job.status.in_(['queued', 'running']), Job.lease_until <= now)
               .order_by(Job.created_at, Job.id).first())
        if row is None:
            return None
        coordinator.token = token
        coordinator.lease_until = now + LEASE_SECONDS
        coordinator.save()
        row.status, row.token, row.lease_until = 'running', token, coordinator.lease_until
        row.attempts += 1
        row.save()
        return row.id


def renew(key, token):
    with _locked() as coordinator:
        now = time.time()
        row = Job.get_or_none(Job.id == key)
        if coordinator.token != token or coordinator.lease_until <= now or row is None or row.token != token:
            return False
        coordinator.lease_until = now + LEASE_SECONDS
        coordinator.save()
        row.lease_until = coordinator.lease_until
        row.save(only=[Job.lease_until])
        return True


def finish(key, token, *, result=None, error=None):
    sealed = _seal(result) if result is not None else None
    with _locked() as coordinator:
        now = time.time()
        row = Job.get_or_none(Job.id == key)
        if coordinator.token != token or coordinator.lease_until <= now or row is None or row.token != token:
            return False
        row.status = 'failed' if error else 'completed'
        row.result, row.error_code = sealed, error or ''
        row.token, row.lease_until, row.expires_at = '', 0, now + RETENTION_SECONDS
        row.save()
        coordinator.token, coordinator.lease_until = '', 0
        coordinator.save()
        return True


async def _heartbeat(key, token):
    while True:
        await asyncio.sleep(8)
        if not await asyncio.to_thread(renew, key, token):
            raise JobError("DOCMIND_ANSWER_LEASE_LOST")


async def _execute(key, token):
    from api.apps.services import docmind_answer_service as answers

    row = await asyncio.to_thread(get_row, key)
    try:
        if int(row["attempts"]) > 2:
            raise JobError("DOCMIND_ANSWER_JOB_INTERRUPTED")
        if time.time() - float(row["created_at"]) > QUEUE_SECONDS:
            raise JobError("DOCMIND_ANSWER_JOB_EXPIRED")
        data = _open(row["data"])
        if await asyncio.to_thread(catalog_fence, data["tenant_id"], data["payload"]) != data["fence"]:
            raise JobError("DOCMIND_SOURCE_CHANGED")
        async with asyncio.timeout(JOB_SECONDS):
            result = await answers.answer(data["tenant_id"], **data["payload"])
        if await asyncio.to_thread(catalog_fence, data["tenant_id"], data["payload"]) != data["fence"]:
            raise JobError("DOCMIND_SOURCE_CHANGED")
        await asyncio.to_thread(finish, key, token, result=result)
    except TimeoutError:
        await asyncio.to_thread(finish, key, token, error="DOCMIND_LLM_TIMEOUT")
    except (answers.AnswerError, JobError) as error:
        await asyncio.to_thread(finish, key, token, error=str(error))
    except Exception:
        logger.warning("DocMind answer job failed")  # Never log provider/source bodies.
        await asyncio.to_thread(finish, key, token, error="DOCMIND_ANSWER_FAILED")


async def run_one(key, token):
    operation = asyncio.create_task(_execute(key, token))
    heartbeat = asyncio.create_task(_heartbeat(key, token))
    try:
        done, _ = await asyncio.wait([operation, heartbeat], return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in (operation, heartbeat):
            task.cancel()
        await asyncio.gather(operation, heartbeat, return_exceptions=True)


async def run_worker():
    while True:
        try:
            token = secrets.token_hex(32)
            key = await asyncio.to_thread(claim, token)
            if key:
                await run_one(key, token)
            else:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise  # Unfinished leases expire; the next process resumes the job.
        except Exception:
            logger.warning("DocMind answer queue temporarily unavailable")
            await asyncio.sleep(3)
