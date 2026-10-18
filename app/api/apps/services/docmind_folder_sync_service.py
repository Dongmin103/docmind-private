"""Folder metadata delivery: independent sequence, shared source ownership fence.

No document/index mutations, file reads or write-authority registration occur here.
Recovery snapshots only nominate missing folders; the host must confirm absence.
"""
from __future__ import annotations

import hashlib
import json
import uuid

from api.apps.services.docmind_change_service import ChangeConflict, _current, _identity, _lock_source, _now, _positive_integer, _IDENTIFIER
from api.db.db_models import (
    DocmindFolderSyncCursor as Cursor,
    DocmindFolderSyncReceipt as Receipt,
    DocmindFolderSyncSnapshot as Snapshot,
    DocmindObservedFolder as Folder,
    DocmindSource,
    DocmindSourceDocument,
)
from common.docmind_source_path import logical_path_identity, logical_path_identity_hash, normalize_logical_relative_path

MUTATIONS = {"batch", "snapshot_begin", "snapshot_end"}


def _invalid():
    raise ChangeConflict("REQUEST_INVALID")


def _path(value, *, root=False):
    if not isinstance(value, str):
        _invalid()
    if root and value == "":
        return ""
    try:
        return normalize_logical_relative_path(value)
    except ValueError as error:
        raise ChangeConflict("REQUEST_INVALID") from error


def _under(path, prefix):
    path, prefix = logical_path_identity(path), logical_path_identity(prefix) if prefix else ""
    return not prefix or path == prefix or path.startswith(prefix + "/")


def _validate(request):
    if not isinstance(request, dict):
        _invalid()
    common = {"protocol_version", "source_id", "worker_id", "owner_id", "epoch", "action"}
    action = request.get("action")
    fields = {
        "status": set(), "batch": {"items"},
        "snapshot_begin": {"snapshot_id", "relative_path"},
        "snapshot_end": {"snapshot_id", "complete"},
        "candidates": {"snapshot_id", "cursor", "limit"},
        "documents": {"relative_path", "cursor", "limit"},
        "identity": {"host_file_id"},
    }
    if action not in fields or type(request.get("protocol_version")) is not int or request["protocol_version"] != 1:
        _invalid()
    required = common | fields[action] | ({"sequence", "request_id"} if action in MUTATIONS else set())
    allowed = required | ({"snapshot_id"} if action == "batch" else set())
    if not required <= request.keys() or request.keys() - allowed:
        _invalid()
    _identity(request["source_id"], request["worker_id"], request["owner_id"])
    if not _positive_integer(request["epoch"]):
        _invalid()
    if action in MUTATIONS and (not _positive_integer(request["sequence"]) or not isinstance(request["request_id"], str) or not _IDENTIFIER.fullmatch(request["request_id"])):
        _invalid()
    if "snapshot_id" in request and (not isinstance(request["snapshot_id"], str) or not _IDENTIFIER.fullmatch(request["snapshot_id"])):
        _invalid()
    if "host_file_id" in request and (not isinstance(request["host_file_id"], str) or not _IDENTIFIER.fullmatch(request["host_file_id"]) or len(request['worker_id']) + len(request['host_file_id']) + 1 > 255):
        _invalid()
    if "relative_path" in request:
        _path(request["relative_path"], root=action == "snapshot_begin")
    if "complete" in request and type(request["complete"]) is not bool:
        _invalid()
    if "limit" in request and (type(request["limit"]) is not int or not 1 <= request["limit"] <= 500):
        _invalid()
    if "cursor" in request and request["cursor"] is not None and (not isinstance(request["cursor"], str) or not _IDENTIFIER.fullmatch(request["cursor"])):
        _invalid()
    if action == "batch":
        if not isinstance(request["items"], list) or not 1 <= len(request["items"]) <= 250:
            _invalid()
        for item in request["items"]:
            if not isinstance(item, dict):
                _invalid()
            fields = {"kind", "relative_path", "generation"}
            if item.get("kind") == "delete":
                fields |= {"root_access_confirmed", "absence_observations"}
                if item.get("root_access_confirmed") is not True or type(item.get("absence_observations")) is not int or item["absence_observations"] < 2:
                    _invalid()
            if set(item) != fields or item["kind"] not in {"upsert", "delete"} or not _positive_integer(item["generation"]):
                _invalid()
            _path(item["relative_path"])
    try:
        encoded = json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as error:
        raise ChangeConflict("REQUEST_INVALID") from error
    if len(encoded) > 1024 * 1024:
        _invalid()
    return hashlib.sha256(encoded).hexdigest()


def _snapshot(request):
    row = Snapshot.get_or_none((Snapshot.source_id == request["source_id"]) & (Snapshot.epoch == request["epoch"]) & (Snapshot.snapshot_id == request["snapshot_id"]))
    if row is None:
        raise ChangeConflict("SNAPSHOT_UNKNOWN")
    return row


def _apply_batch(request, source, now):
    snapshot = _snapshot(request) if "snapshot_id" in request else None
    if snapshot is not None and snapshot.failed:
        raise ChangeConflict("SNAPSHOT_INCOMPLETE")
    if snapshot is not None and snapshot.complete:
        raise ChangeConflict("SNAPSHOT_COMPLETE")
    states = []
    for item in request["items"]:
        path = _path(item["relative_path"])
        if snapshot is not None and (item["kind"] != "upsert" or not _under(path, snapshot.relative_path)):
            _invalid()
        fence = (request["epoch"], item["generation"])
        if item["kind"] == "upsert":
            parts = path.split("/")
            for depth in range(1, len(parts) + 1):
                parent = "/".join(parts[:depth])
                row = Folder.get_or_none((Folder.source_id == source.id) & (Folder.relative_path_hash == logical_path_identity_hash(parent)))
                # Absence watermarks survive recreation. Otherwise a late,
                # previously unseen descendant can reappear under a new parent.
                if row is not None and (row.absence_epoch, row.absence_generation) >= fence:
                    break
                if row is not None and (row.observation_epoch, row.observation_generation) > fence:
                    continue
                values = dict(relative_path=parent, observation_epoch=fence[0], observation_generation=fence[1],
                              last_sequence=request["sequence"], deleted_at=None)
                if snapshot is not None:
                    values["last_snapshot_id"] = snapshot.id
                if row is None:
                    Folder.create(id=uuid.uuid4().hex, project_id=source.project_id, source_id=source.id,
                                  relative_path_hash=logical_path_identity_hash(parent), **values)
                else:
                    Folder.update(**values).where(Folder.id == row.id).execute()
        else:
            # Keep a tombstone even when no prior observation exists: stale
            # document paths must not synthesize this folder again.
            own = Folder.get_or_none((Folder.source_id == source.id) & (Folder.relative_path_hash == logical_path_identity_hash(path)))
            if own is None:
                Folder.create(id=uuid.uuid4().hex, project_id=source.project_id, source_id=source.id,
                              relative_path=path, relative_path_hash=logical_path_identity_hash(path),
                              observation_epoch=fence[0], observation_generation=fence[1], absence_epoch=fence[0], absence_generation=fence[1], last_sequence=request["sequence"], deleted_at=now)
            for row in Folder.select().where(Folder.source_id == source.id).iterator():
                if _under(row.relative_path, path) and (row.observation_epoch, row.observation_generation) <= fence:
                    Folder.update(deleted_at=now, observation_epoch=fence[0], observation_generation=fence[1], absence_epoch=fence[0], absence_generation=fence[1], last_sequence=request["sequence"]).where(Folder.id == row.id).execute()
        states.append({"relative_path": path, "state": "OBSERVED" if item["kind"] == "upsert" else "ABSENT"})
    return {"items": states}


def _read_page(request):
    if request["action"] == "candidates":
        snap = _snapshot(request)
        if not snap.complete:
            raise ChangeConflict("SNAPSHOT_INCOMPLETE")
        query = Folder.select().where((Folder.source_id == request["source_id"]) & Folder.deleted_at.is_null(True)
            & (Folder.last_sequence <= snap.start_sequence) & (Folder.last_snapshot_id.is_null(True) | (Folder.last_snapshot_id != snap.id)))
        prefix = snap.relative_path
        key, columns = Folder.id, ("relative_path",)
    else:
        query = DocmindSourceDocument.select().where((DocmindSourceDocument.source_id == request["source_id"]) & DocmindSourceDocument.deleted_at.is_null(True))
        prefix = _path(request["relative_path"])
        key, columns = DocmindSourceDocument.document_id, ("document_id", "relative_path")
    if request["cursor"] is not None:
        query = query.where(key > request["cursor"])
    # Database LOWER/collation is not Unicode casefold (e.g. Straße/STRASSE).
    # Stream only metadata from this source, returning a bounded subtree page.
    rows = []
    for row in query.order_by(key).iterator():
        if _under(row.relative_path, prefix):
            rows.append(row)
            if len(rows) > request["limit"]:
                break
    page = rows[:request["limit"]]
    candidates = [{name: getattr(row, name) for name in columns} for row in page]
    if request["action"] == "documents":
        for item, row in zip(candidates, page):
            if row.host_file_identity:
                item["host_file_identity"] = row.host_file_identity
    return {"candidates": candidates,
            "next_cursor": getattr(page[-1], key.name) if len(rows) > len(page) else None}


def receive(request, *, now=None):
    digest = _validate(request)
    # The existing source row lock serializes both channels while their durable
    # sequence and receipt state remain independent.
    with DocmindSource._meta.database.atomic():
        _lock_source(request["source_id"])
        now = now or _now()
        _current(request["source_id"], request["worker_id"], request["owner_id"], request["epoch"], now)
        source = DocmindSource.get_by_id(request["source_id"])
        cursor = Cursor.get_or_none(Cursor.source_id == source.id)
        last = cursor.last_sequence if cursor is not None and cursor.epoch == request["epoch"] else 0
        if request["action"] == "status":
            return {"epoch": request["epoch"], "last_sequence": last}
        if request['action'] == 'identity':
            rows = list(DocmindSourceDocument.select(DocmindSourceDocument.relative_path).where(
                (DocmindSourceDocument.source_id == source.id) & (DocmindSourceDocument.project_id == source.project_id)
                & (DocmindSourceDocument.host_file_identity == request['worker_id'] + ':' + request['host_file_id'])
                & DocmindSourceDocument.deleted_at.is_null(True)).limit(2))
            return {'relative_path': rows[0].relative_path if len(rows) == 1 else None}
        if request["action"] not in MUTATIONS:
            return _read_page(request)
        old = Receipt.get_or_none((Receipt.source_id == source.id) & (Receipt.epoch == request["epoch"]) & (Receipt.request_id == request["request_id"]))
        if old is not None:
            if old.payload_sha256 != digest or old.sequence != request["sequence"]:
                raise ChangeConflict("REQUEST_CONFLICT")
            return json.loads(old.result_json)
        if request["sequence"] <= last:
            raise ChangeConflict("SEQUENCE_REPLAY")
        if request["sequence"] != last + 1:
            raise ChangeConflict("SEQUENCE_GAP")
        if request["action"] == "batch":
            data = _apply_batch(request, source, now)
        elif request["action"] == "snapshot_begin":
            if Snapshot.get_or_none((Snapshot.source_id == source.id) & (Snapshot.epoch == request["epoch"]) & (Snapshot.snapshot_id == request["snapshot_id"])) is not None:
                raise ChangeConflict("SNAPSHOT_CONFLICT")
            Snapshot.create(id=uuid.uuid4().hex, source_id=source.id, epoch=request["epoch"], snapshot_id=request["snapshot_id"],
                            relative_path=_path(request["relative_path"], root=True), start_sequence=request["sequence"])
            data = {}
        else:
            snap = _snapshot(request)
            if snap.complete:
                raise ChangeConflict("SNAPSHOT_COMPLETE")
            if snap.failed:
                raise ChangeConflict("SNAPSHOT_INCOMPLETE")
            Snapshot.update(complete=request["complete"], failed=not request["complete"]).where(Snapshot.id == snap.id).execute()
            data = {}
        result = {"accepted": True, "epoch": request["epoch"], "sequence": request["sequence"], **data}
        Receipt.create(id=uuid.uuid4().hex, source_id=source.id, epoch=request["epoch"], sequence=request["sequence"],
                       request_id=request["request_id"], payload_sha256=digest, result_json=json.dumps(result))
        if cursor is None:
            Cursor.create(source_id=source.id, epoch=request["epoch"], last_sequence=request["sequence"])
        else:
            Cursor.update(epoch=request["epoch"], last_sequence=request["sequence"]).where(Cursor.source_id == source.id).execute()
        return result
