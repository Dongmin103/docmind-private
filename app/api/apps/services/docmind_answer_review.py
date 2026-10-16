"""Frozen, source-free message and action helpers for reviewed DocMind answers."""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

ASSET_HASHES = {
    "shared-grounding-candidate-2026-10-15.json": "3f1fd140436863f36e28eed99fe6fc968f8cb3a3bad01f17b8645d3b597c396b",
    "coverage-message-policy-2026-10-15.json": "6ef3dee8a76de3436544aa8f5828cddee9ff6c822504358aa823a7c956475fd9",
    "answer-response-contract-2026-10-15.txt": "a404dd5a31dd7681fae4cca18d018f1b91ea0e3bd3435b1bfa63f971f00a0921",
    "scope-example-2026-10-15.json": "c8f93c15276f79e0c550037575ba96a2ced919fdcc86c2273e492d70a4845e9f",
    "review-policy-round3.json": "95ad5878434943c94dac1d3c9aaf55573d6ad2eda2de8e999a8cd7b0f5f7fb18",
    "review-calibration-round7.json": "d81b781b155c7942bf8311971e8229f75c5f1bedfa6c6c4a019059dcfa4e2965",
}


@dataclass(frozen=True)
class Assets:
    common_policy: str
    generation_task: str
    answer_contract: str
    scope_example: list[dict]
    review_policy: str
    calibration: list[dict]


def load_assets() -> Assets:
    """Fail closed on a missing or changed packaged asset before model calls."""
    root = Path(__file__).with_name("docmind_answer_assets")
    raw = {}
    for name, digest in ASSET_HASHES.items():
        content = (root / name).read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("DOCMIND_ANSWER_ASSET_INVALID")
        raw[name] = content.decode("utf-8")
    common = json.loads(raw["shared-grounding-candidate-2026-10-15.json"])["system"]
    task = json.loads(raw["coverage-message-policy-2026-10-15.json"])["generation_task"]
    example = json.loads(raw["scope-example-2026-10-15.json"])
    calibration = json.loads(raw["review-calibration-round7.json"])
    policy = json.loads(raw["review-policy-round3.json"])
    if not isinstance(example, list) or not isinstance(calibration, list) or len(calibration) != 4:
        raise ValueError("DOCMIND_ANSWER_ASSET_INVALID")
    if [m.get("role") for m in calibration] != ["user", "assistant", "user", "assistant"]:
        raise ValueError("DOCMIND_ANSWER_ASSET_INVALID")
    return Assets(common, task, raw["answer-response-contract-2026-10-15.txt"], example, policy, calibration)


def generation_messages(common_policy, question, chunks, task, response_contract):
    documents, keys, evidence = [], {}, []
    for i, chunk in enumerate(chunks, 1):
        identity = (chunk.get("doc_id"), chunk.get("docnm_kwd", ""))
        if identity not in keys:
            keys[identity] = f"d{len(keys) + 1}"
            documents.append({"document_key": keys[identity], "name": chunk.get("docnm_kwd", "")})
        evidence.append({"evidence_id": i, "document_key": keys[identity], "text": chunk["content_with_weight"]})
    return [{"role": "system", "content": common_policy}, {"role": "user", "content": json.dumps({
        "documents": documents, "evidence": evidence, "question": question,
        "answer_task": task, "response_contract": response_contract,
    }, ensure_ascii=False)}]


def with_scope_example(messages, scope_example):
    return messages[:1] + scope_example + messages[1:]


def external_review_messages(generation_prefix, draft, question, policy):
    units = [{"id": f"l{i + 1}", "text": line} for i, line in enumerate(draft["answer"].split("\n")) if line.strip()]
    return [*generation_prefix, {"role": "user", "content": json.dumps({
        "draft_status": draft["status"], "draft_citations": draft["citations"],
        "draft_lines": units, "question": question, "review_task": policy,
    }, ensure_ascii=False)}]


def make_generation_schema(citation_count):
    if type(citation_count) is not int or citation_count < 1:
        raise ValueError("citation_count must be a positive integer")
    return {
        "type": "json_schema",
        "json_schema": {"name": "grounded_answer", "strict": True, "schema": {
            "type": "object", "properties": {
                "status": {"type": "string", "enum": ["answered", "partial", "insufficient"]},
                "answer": {"type": "string"},
                "citations": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": citation_count}},
            }, "required": ["status", "answer", "citations"], "additionalProperties": False,
        }},
    }


def make_review_schema(draft, citation_count):
    if type(citation_count) is not int or citation_count < 1:
        raise ValueError("citation_count must be a positive integer")
    line_ids = [f"l{i + 1}" for i, line in enumerate(draft["answer"].split("\n")) if line.strip()]
    if not line_ids:
        raise ValueError("draft must contain a non-empty line")
    cite_properties = {line_id: {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": citation_count}, "minItems": 1} for line_id in line_ids}
    change_properties = {line_id: {"type": "string"} for line_id in line_ids}
    choices = [
        {"type": "object", "properties": {"action": {"type": "string", "enum": ["keep"]}}, "required": ["action"], "additionalProperties": False},
        {"type": "object", "properties": {"action": {"type": "string", "enum": ["cite"]}, "citations": {"type": "object", "properties": cite_properties, "additionalProperties": False}}, "required": ["action", "citations"], "additionalProperties": False},
        {"type": "object", "properties": {"action": {"type": "string", "enum": ["patch"]}, "status": {"type": "string", "enum": ["answered", "partial", "insufficient"]}, "changes": {"type": "object", "properties": change_properties, "additionalProperties": False}, "additions": {"type": "array", "items": {"type": "string"}}}, "required": ["action", "status", "changes", "additions"], "additionalProperties": False},
    ]
    return {"type": "json_schema", "json_schema": {"name": "review_action", "strict": True, "schema": {"anyOf": choices}}}


def apply_line_changes(answer, patch, citation_count):
    if not isinstance(patch, dict) or set(patch) != {"status", "changes", "additions"} or not isinstance(patch["changes"], list) or not isinstance(patch["additions"], list):
        raise ValueError("Invalid review schema")
    lines = answer.split("\n")
    valid = {f"l{i + 1}": i for i, line in enumerate(lines) if line.strip()}
    seen = set()
    for change in patch["changes"]:
        if not isinstance(change, dict) or set(change) != {"id", "text"} or change["id"] not in valid or change["id"] in seen or not isinstance(change["text"], str):
            raise ValueError("Invalid review change")
        seen.add(change["id"])
        lines[valid[change["id"]]] = change["text"]
    if any(not isinstance(text, str) for text in patch["additions"]):
        raise ValueError("Invalid addition")
    if patch["status"] == "insufficient":
        if patch["changes"] or patch["additions"]:
            raise ValueError("Unexpected insufficient edits")
        return {"status": "insufficient", "answer": "", "citations": []}
    updated = "\n".join(lines)
    if patch["additions"]:
        updated += "\n\n" + "\n\n".join(patch["additions"])
    refs = sorted({int(ref) for ref in re.findall(r"\[(\d+)\]", updated)})
    if not updated.strip() or not refs or any(not 1 <= ref <= citation_count for ref in refs):
        raise ValueError("Invalid reviewed citations")
    return {"status": patch["status"], "answer": updated, "citations": refs}


def apply_change_map(answer, patch, citation_count):
    if set(patch) != {"status", "changes", "additions"} or not isinstance(patch["changes"], dict):
        raise ValueError("Invalid change map")
    valid = {f"l{i + 1}" for i, line in enumerate(answer.split("\n")) if line.strip()}
    if not set(patch["changes"]) <= valid:
        raise ValueError("Unknown line id")
    changes = [{"id": key, "text": text} for key, text in patch["changes"].items() if text is not None]
    return apply_line_changes(answer, {"status": patch["status"], "changes": changes, "additions": patch["additions"]}, citation_count)


def apply_review_action(draft, response, citation_count):
    if not isinstance(response, dict):
        raise TypeError("Invalid action response")
    if response.get("action") == "keep":
        if set(response) != {"action"}:
            raise ValueError("Unexpected keep fields")
        return {key: draft[key] for key in ("status", "answer", "citations")}
    if response.get("action") == "cite":
        refs_by_line = response.get("citations")
        if set(response) != {"action", "citations"} or not isinstance(refs_by_line, dict) or not refs_by_line or draft["status"] not in {"answered", "partial"}:
            raise ValueError("Invalid citation-only action")
        lines = {f"l{i + 1}": line for i, line in enumerate(draft["answer"].split("\n")) if line.strip()}
        changes = {}
        for line_id, refs in refs_by_line.items():
            if not isinstance(line_id, str) or line_id not in lines or re.search(r"\[\d+\]", lines[line_id]):
                raise ValueError("Citation-only action cannot replace a cited or unknown line")
            if not isinstance(refs, list) or not refs or any(type(ref) is not int or not 1 <= ref <= citation_count for ref in refs) or len(set(refs)) != len(refs):
                raise ValueError("Invalid citation-only references")
            changes[line_id] = lines[line_id] + " " + " ".join(f"[{ref}]" for ref in refs)
        return apply_change_map(draft["answer"], {"status": draft["status"], "changes": changes, "additions": []}, citation_count)
    if response.get("action") != "patch" or set(response) != {"action", "status", "changes", "additions"}:
        raise ValueError("Invalid patch action")
    return apply_change_map(draft["answer"], {key: response[key] for key in ("status", "changes", "additions")}, citation_count)


def normalize_review_result(display_draft, action, citation_count):
    """Keep the measured insufficient review line, then restore raw JSON semantics."""
    result = apply_review_action(display_draft, action, citation_count)
    if action.get("action") == "keep" and display_draft["status"] == "insufficient":
        if display_draft["citations"]:
            raise ValueError("Invalid insufficient draft")
        return {"status": "insufficient", "answer": "", "citations": []}
    return result


class ContextTooLarge(ValueError):
    pass


class RecoverableOutput(Exception):
    """A malformed generation or review response, eligible for one shared retry."""


async def bounded_generate_review(generate_once, review_once, verify_context):
    """Two normal calls and at most one retry; never expose an unreviewed draft."""
    recovery_used = False
    while True:
        try:
            draft = await generate_once()
            break
        except RecoverableOutput:
            if recovery_used:
                raise
            recovery_used = True
            await verify_context()
    await verify_context()
    while True:
        try:
            final = await review_once(draft)
            break
        except RecoverableOutput:
            if recovery_used:
                raise
            recovery_used = True
            await verify_context()
    await verify_context()
    return final


def pack_candidate(question, seeds, expanded, input_budget, count_tokens, common_policy):
    """Match the measured seed-first/whole-neighbor selection; never drop a seed."""
    key = lambda row: (str(row["doc_id"]), str(row["chunk_id"]))
    identity = lambda row: tuple(row.get(field) for field in ("doc_id", "kb_id", "parse_run_id", "chunk_set_id"))
    selected = {key(seed): seed for seed in seeds}
    extras = [row for row in expanded if key(row) not in selected]
    extras.sort(key=lambda row: min((abs(row["chunk_order_int"] - seed["chunk_order_int"]), rank)
                                    for rank, seed in enumerate(seeds)
                                    if identity(row) == identity(seed) and type(seed.get("chunk_order_int")) is int
                                    and seed.get("parse_run_id") and seed.get("chunk_set_id")))
    prioritized = [*selected.values(), *extras]
    order = {key(row): i for i, row in enumerate(expanded)}
    def materialize(n):
        rows = sorted(prioritized[:n], key=lambda row: order[key(row)])
        evidence = [{"evidence_id": i + 1, "text": row["content_with_weight"]} for i, row in enumerate(rows)]
        messages = [{"role": "system", "content": common_policy},
                    {"role": "user", "content": json.dumps({"question": question, "evidence": evidence}, ensure_ascii=False)}]
        return rows, messages, count_tokens(messages)
    rows, messages, count = materialize(len(prioritized))
    if count <= input_budget:
        return rows, messages, count
    lower, upper = len(selected), len(prioritized)
    best = materialize(lower)
    if best[2] > input_budget:
        raise ContextTooLarge("DOCMIND_CONTEXT_TOO_LARGE")
    while lower < upper:
        middle = (lower + upper + 1) // 2
        trial = materialize(middle)
        if trial[2] <= input_budget:
            best, lower = trial, middle
        else:
            upper = middle - 1
    return best
