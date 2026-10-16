"""Synthetic checks for the staged reviewed answer path; no network or source data."""

import asyncio
import hashlib
import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path

SERVICES = Path(__file__).resolve().parents[3] / "api/apps/services"


@contextmanager
def raises(error_type):
    try:
        yield
    except error_type:
        return
    raise AssertionError(f"Expected {error_type.__name__}")


def load(name):
    path = SERVICES / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_chunk(order, doc="doc"):
    return {"doc_id": doc, "docnm_kwd": doc, "chunk_id": f"{doc}-{order}",
            "chunk_order_int": order, "kb_id": "kb", "parse_run_id": "run",
            "chunk_set_id": "set", "content_with_weight": f"evidence {order}"}


def test_frozen_assets_and_generation_message_contract():
    review = load("docmind_answer_review")
    assets = review.load_assets()
    assert len(assets.calibration) == 4
    chunks = [synthetic_chunk(1), synthetic_chunk(2, "other")]
    messages = review.generation_messages(assets.common_policy, "q", chunks,
                                          assets.generation_task, assets.answer_contract)
    assert messages[0] == {"role": "system", "content": assets.common_policy}
    payload = json.loads(messages[1]["content"])
    assert [r["evidence_id"] for r in payload["evidence"]] == [1, 2]
    assert [r["document_key"] for r in payload["documents"]] == ["d1", "d2"]
    assert payload["answer_task"] == assets.generation_task
    assert payload["response_contract"] == assets.answer_contract
    complete = review.with_scope_example(messages, assets.scope_example)
    assert len(complete) == 4
    assert [row["role"] for row in complete] == ["system", "user", "assistant", "user"]
    assert complete[-1] == messages[-1]


def test_review_schema_and_application_fail_closed():
    review = load("docmind_answer_review")
    draft = {"status": "partial", "answer": "A [1]\nB [2]", "citations": [1, 2]}
    schema = review.make_review_schema(draft, 2)
    assert schema["type"] == "json_schema"
    assert set(schema["json_schema"]["schema"]) == {"anyOf"}
    assert review.apply_review_action(draft, {"action": "keep"}, 2) == draft
    fixed = review.apply_review_action(draft, {"action": "patch", "status": "partial",
                                                "changes": {"l1": "Fixed [1]"}, "additions": []}, 2)
    assert fixed["answer"] == "Fixed [1]\nB [2]"
    uncited = {"status": "partial", "answer": "A\nB [2]", "citations": [2]}
    cited = review.apply_review_action(uncited, {"action": "cite", "citations": {"l1": [1]}}, 2)
    assert cited == {"status": "partial", "answer": "A [1]\nB [2]", "citations": [1, 2]}
    with raises(ValueError):
        review.apply_review_action(draft, {"action": "cite", "citations": {"l1": [2]}}, 2)
    with raises(ValueError):
        review.apply_review_action(draft, {"action": "patch", "status": "answered",
                                                 "changes": {"l3": "invented [1]"}, "additions": []}, 2)


def test_review_schema_supports_expanded_evidence_and_rejects_invalid_counts():
    review = load("docmind_answer_review")
    draft = {"status": "partial", "answer": "A [1]\nB [2]", "citations": [1, 2]}
    compact = review.make_review_schema(draft, 2)
    compact_sha = hashlib.sha256(json.dumps(compact, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    assert compact_sha == "731f11e3eee56b039701f8f464a7612065ea864544034c594f4f86686f6121d7"

    for count in (129, 176):
        schema = review.make_review_schema(draft, count)["json_schema"]["schema"]
        cited = schema["anyOf"][1]["properties"]["citations"]["properties"]["l1"]
        assert cited["items"]["maximum"] == count
        assert schema["anyOf"][2]["properties"]["changes"]["properties"] == {
            "l1": {"type": "string"}, "l2": {"type": "string"}}

    for invalid in (0, -1, True, False, 1.0, "176"):
        with raises(ValueError):
            review.make_review_schema(draft, invalid)
    with raises(ValueError):
        review.make_review_schema({"status": "insufficient", "answer": "\n", "citations": []}, 1)


def test_pack_keeps_all_seeds_and_rejects_overflow_without_truncation():
    review = load("docmind_answer_review")
    seeds = [synthetic_chunk(5), synthetic_chunk(7)]
    expanded = [synthetic_chunk(i) for i in (3, 4, 5, 6, 7, 8, 9)]
    def count(messages):
        payload = json.loads(messages[-1]["content"])
        return len(payload["evidence"]) * 10
    rows, _, count_used = review.pack_candidate("q", seeds, expanded, 50, count, "synthetic policy")
    assert {r["chunk_id"] for r in seeds} <= {r["chunk_id"] for r in rows}
    assert count_used <= 50
    assert [r["chunk_order_int"] for r in rows] == sorted(r["chunk_order_int"] for r in rows)
    with raises(review.ContextTooLarge):
        review.pack_candidate("q", seeds, expanded, 10, count, "synthetic policy")


def test_review_user_contract_keeps_generation_prefix_and_one_draft():
    review = load("docmind_answer_review")
    draft = {"status": "answered", "answer": "A [1]", "citations": [1]}
    prefix = [{"role": "system", "content": "synthetic"}]
    messages = review.external_review_messages([*prefix, {"role": "user", "content": "calib"}],
                                               draft, "q", "generic review")
    assert messages[:2] == [*prefix, {"role": "user", "content": "calib"}]
    user = json.loads(messages[-1]["content"])
    assert user["draft_lines"] == [{"id": "l1", "text": "A [1]"}]
    assert user["draft_status"] == "answered" and user["draft_citations"] == [1]
    assert user["review_task"] == "generic review"


def test_recovery_budget_covers_generation_or_review_only_once():
    review = load("docmind_answer_review")
    async def scenario(gen_fail=False, rev_fail=False, source_fail=False):
        calls = []
        async def generate():
            calls.append("gen")
            if gen_fail and calls.count("gen") == 1:
                raise review.RecoverableOutput("generation")
            return {"status": "answered", "answer": "A [1]", "citations": [1]}
        async def inspect(draft):
            calls.append("review")
            if rev_fail and calls.count("review") == 1:
                raise review.RecoverableOutput("review")
            return draft
        async def verify():
            calls.append("verify")
            if source_fail:
                raise ValueError("source changed")
        try:
            result = await review.bounded_generate_review(generate, inspect, verify)
            return calls, result, None
        except (review.RecoverableOutput, ValueError) as error:
            return calls, None, error
    calls, result, error = asyncio.run(scenario())
    assert error is None and result["answer"] == "A [1]" and calls.count("gen") + calls.count("review") == 2
    calls, result, error = asyncio.run(scenario(gen_fail=True))
    assert error is None and result is not None and calls.count("gen") + calls.count("review") == 3
    calls, result, error = asyncio.run(scenario(rev_fail=True))
    assert error is None and result is not None and calls.count("gen") + calls.count("review") == 3
    calls, result, error = asyncio.run(scenario(gen_fail=True, rev_fail=True))
    assert isinstance(error, review.RecoverableOutput) and result is None and calls.count("gen") + calls.count("review") == 3
    calls, result, error = asyncio.run(scenario(gen_fail=True, source_fail=True))
    assert isinstance(error, ValueError) and result is None and calls.count("gen") == 1


def test_insufficient_review_uses_display_line_but_keeps_raw_empty_contract():
    review = load("docmind_answer_review")
    draft_for_wire = {"status": "insufficient", "answer": "근거 부족 안내", "citations": []}
    message = review.external_review_messages([], draft_for_wire, "q", "policy")[-1]
    assert json.loads(message["content"])["draft_lines"] == [{"id": "l1", "text": "근거 부족 안내"}]
    assert "l1" in review.make_review_schema(draft_for_wire, 1)["json_schema"]["schema"]["anyOf"][2]["properties"]["changes"]["properties"]
    assert review.normalize_review_result(draft_for_wire, {"action": "keep"}, 1) == {
        "status": "insufficient", "answer": "", "citations": []}


if __name__ == "__main__":
    for name, item in sorted(globals().items()):
        if name.startswith("test_") and callable(item):
            item()
            print(f"PASS {name}")
