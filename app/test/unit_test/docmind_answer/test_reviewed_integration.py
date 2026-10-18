"""Staged reviewed-path boundaries with synthetic evidence and no external calls."""

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import httpx
import pytest

SERVICES = Path(__file__).resolve().parents[3] / "api/apps/services"


def load(name, monkeypatch):
    spec = importlib.util.spec_from_file_location(name, SERVICES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def sample_chunk(index=1):
    return {"chunk_id": f"c{index}", "doc_id": "d", "docnm_kwd": "synthetic",
            "content_with_weight": f"synthetic evidence {index}", "chunk_order_int": index,
            "parse_run_id": "r", "chunk_set_id": "s", "kb_id": "kb"}


@pytest.fixture
def reviewed(service, monkeypatch):
    review = load("docmind_answer_review", monkeypatch)
    state = {"search": [], "generate": 0, "review": 0, "verify": 0, "fail_gen_once": False,
             "fail_review_once": False, "always_bad_review": False, "source_changes": False,
             "insufficient": False, "last_review_messages": None}
    packed = [sample_chunk()]

    async def search(tenant, question, **kwargs):
        state["search"].append(kwargs)
        return {"dataset_id": "kb", "chunks": packed, "trace_id": "trace",
                "catalog_version_id": "v1", "scope_doc_ids": ["d"]}

    fake_search = types.SimpleNamespace(search=search)
    modules = types.ModuleType("api.apps.services")
    modules.docmind_answer_service = service
    modules.docmind_answer_review = review
    modules.docmind_api_service = fake_search
    monkeypatch.setitem(sys.modules, "api.apps.services", modules)
    pipeline = load("docmind_review_pipeline", monkeypatch)
    state["real_generate"] = service.generate
    state["real_review"] = pipeline._review_once
    state["real_prepare_context"] = pipeline._prepare_context
    state["real_check_review_tokens"] = pipeline._check_review_tokens
    monkeypatch.setitem(sys.modules, "api.apps.services.docmind_review_pipeline", pipeline)
    modules.docmind_review_pipeline = pipeline
    monkeypatch.setattr(service, "_load_context", lambda *_: (packed, packed))

    def verify(*_):
        state["verify"] += 1
        if state["source_changes"] and state["verify"] >= 2:
            raise service.AnswerError("DOCMIND_SOURCE_CHANGED")
    monkeypatch.setattr(service, "_verify_context", verify)
    monkeypatch.setattr(pipeline, "_prepare_context", lambda *_: (packed, [{"role": "system", "content": "synthetic"}], 32768))
    monkeypatch.setattr(pipeline, "_check_review_tokens", lambda *_: None)

    async def generate(*_args, **_kwargs):
        state["generate"] += 1
        if state["fail_gen_once"] and state["generate"] == 1:
            raise service.AnswerError("DOCMIND_LLM_RESPONSE_INVALID")
        if state["insufficient"]:
            return service._GeneratedAnswer("insufficient", service._INSUFFICIENT_NOTICE, ())
        return service._GeneratedAnswer("answered", "synthetic answer [1]", (1,))

    async def inspect(_config, messages, draft, count):
        state["review"] += 1
        state["last_review_messages"] = messages
        if state["always_bad_review"] or (state["fail_review_once"] and state["review"] == 1):
            raise service.AnswerError("DOCMIND_LLM_RESPONSE_INVALID")
        raw = review.normalize_review_result(draft, {"action": "keep"}, count)
        return service._validate_generated_answer(json.dumps(raw), count)

    monkeypatch.setattr(service, "generate", generate)
    monkeypatch.setattr(pipeline, "_review_once", inspect)
    monkeypatch.setenv("DOCMIND_ANSWER_ENABLED", "1")
    monkeypatch.setenv("DOCMIND_ANSWER_PIPELINE", "reviewed_v1")
    monkeypatch.setenv("DOCMIND_ANSWER_PROVIDER", "openai")
    monkeypatch.setenv("DOCMIND_ANSWER_MODEL", "qwen3.8-flash-next")
    monkeypatch.setenv("DOCMIND_ANSWER_OUTPUT_TOKENS", "2048")
    monkeypatch.setenv("DOCMIND_ANSWER_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setenv("DOCMIND_REVIEWED_ALLOWED_BASE_URL", "http://localhost:1234/v1")
    return state, pipeline


def test_answer_uses_internal_top16_and_review_before_delivery(service, reviewed):
    state, _ = reviewed
    result = asyncio.run(service.answer("tenant", "question", scope={"mode": "all"}))
    assert state["search"] == [{"project_id": None, "scope": {"mode": "all"}, "answer_mode": True}]
    assert (state["generate"], state["review"]) == (1, 1)
    assert result["answer"] == "synthetic answer [1]"
    assert result["citations"][0]["chunk_id"] == "c1"
    assert result["context"]["seed_count"] == 1
    assert set(result["timings_ms"]) == {"search", "context", "pack", "generation", "review", "verify"}


def test_generation_repair_then_review_stays_at_three_calls(service, reviewed):
    state, _ = reviewed
    state["fail_gen_once"] = True
    result = asyncio.run(service.answer("tenant", "question", scope={"mode": "all"}))
    assert result["answer_status"] == "answered"
    assert (state["generate"], state["review"]) == (2, 1)


def test_review_repair_then_delivers_only_validated_final(service, reviewed):
    state, _ = reviewed
    state["fail_review_once"] = True
    result = asyncio.run(service.answer("tenant", "question", scope={"mode": "all"}))
    assert result["answer_status"] == "answered"
    assert (state["generate"], state["review"]) == (1, 2)


def test_generation_repair_spends_shared_budget_and_invalid_review_is_not_draft_fallback(service, reviewed):
    state, _ = reviewed
    state["fail_gen_once"] = True
    state["always_bad_review"] = True
    with pytest.raises(service.AnswerError, match="DOCMIND_LLM_RESPONSE_INVALID"):
        asyncio.run(service.answer("tenant", "question", scope={"mode": "all"}))
    assert (state["generate"], state["review"]) == (2, 1)


def test_source_change_blocks_retry_and_answer(service, reviewed):
    state, _ = reviewed
    state["fail_gen_once"] = True
    state["source_changes"] = True
    with pytest.raises(service.AnswerError, match="DOCMIND_SOURCE_CHANGED"):
        asyncio.run(service.answer("tenant", "question", scope={"mode": "all"}))
    assert (state["generate"], state["review"]) == (1, 0)


def test_generation_and_review_share_one_ten_minute_deadline(service, reviewed, monkeypatch):
    state, pipeline = reviewed
    assert pipeline.MODEL_DEADLINE_SECONDS == 600
    monkeypatch.setattr(pipeline, "MODEL_DEADLINE_SECONDS", 0.01)

    async def slow_generate(*_args, **_kwargs):
        state["generate"] += 1
        await asyncio.sleep(0.05)
        return service._GeneratedAnswer("answered", "synthetic [1]", (1,))

    monkeypatch.setattr(service, "generate", slow_generate)
    with pytest.raises(service.AnswerError, match="DOCMIND_LLM_TIMEOUT"):
        asyncio.run(service.answer("tenant", "question", scope={"mode": "all"}))
    assert (state["generate"], state["review"]) == (1, 0)


def test_insufficient_review_wire_keeps_measured_display_line_and_final_notice(service, reviewed):
    state, _ = reviewed
    state["insufficient"] = True
    result = asyncio.run(service.answer("tenant", "question", scope={"mode": "all"}))
    review_task = json.loads(state["last_review_messages"][-1]["content"])
    assert review_task["draft_status"] == "insufficient"
    assert review_task["draft_citations"] == []
    assert review_task["draft_lines"] == [{"id": "l1", "text": service._INSUFFICIENT_NOTICE}]
    assert result["answer_status"] == "insufficient"
    assert result["answer"] == service._INSUFFICIENT_NOTICE and result["citations"] == []


@pytest.mark.parametrize("provider,output,context", [("ollama", "2048", "32768"), ("openrouter", "2048", "32768"),
                                                    ("openai", "1024", "32768"), ("openai", "2048", "8192")])
def test_reviewed_mode_requires_local_openai_profile(service, reviewed, monkeypatch, provider, output, context):
    monkeypatch.setenv("DOCMIND_ANSWER_PROVIDER", provider)
    monkeypatch.setenv("DOCMIND_ANSWER_OUTPUT_TOKENS", output)
    monkeypatch.setenv("DOCMIND_ANSWER_CONTEXT_TOKENS", context)
    with pytest.raises(service.AnswerError, match="DOCMIND_ANSWER_CONFIG_INVALID"):
        service.AnswerConfig.from_env()


def test_reviewed_mode_rejects_unpinned_or_changed_server(service, reviewed, monkeypatch):
    monkeypatch.delenv("DOCMIND_REVIEWED_ALLOWED_BASE_URL")
    with pytest.raises(service.AnswerError, match="DOCMIND_ANSWER_CONFIG_INVALID"):
        service.AnswerConfig.from_env()
    monkeypatch.setenv("DOCMIND_REVIEWED_ALLOWED_BASE_URL", "http://localhost:2345/v1")
    with pytest.raises(service.AnswerError, match="DOCMIND_ANSWER_CONFIG_INVALID"):
        service.AnswerConfig.from_env()


@pytest.mark.parametrize("output", [2048, 4096])
def test_generation_and_review_wire_match_frozen_reasoning_schema(service, reviewed, monkeypatch, output):
    state, pipeline = reviewed
    monkeypatch.setenv("DOCMIND_ANSWER_OUTPUT_TOKENS", str(output))
    config = service.AnswerConfig.from_env()
    requests = []
    original_client = httpx.AsyncClient

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            content = {"status": "answered", "answer": "synthetic answer [1]", "citations": [1]}
        else:
            content = {"action": "keep"}
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": json.dumps(content)}}]})

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(pipeline.httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport))

    async def run():
        async with original_client(transport=transport) as client:
            generated = await state["real_generate"](config, [{"role": "system", "content": "synthetic"}], 1, client=client)
        draft = {"status": generated.status, "answer": generated.answer, "citations": list(generated.references)}
        return await state["real_review"](config, [{"role": "system", "content": "synthetic"}], draft, 1)

    final = asyncio.run(run())
    assert final.answer == "synthetic answer [1]" and len(requests) == 2
    generation, inspection = requests
    assert generation["response_format"] == service._generation_response_format(1)
    assert inspection["response_format"] == pipeline.review.make_review_schema(
        {"status": "answered", "answer": "synthetic answer [1]", "citations": [1]}, 1)
    for payload, cache in ((generation, False), (inspection, True)):
        assert payload["cache_prompt"] is cache
        assert payload["max_tokens"] == output and payload["temperature"] == 0
        assert payload["reasoning_effort"] == "low" and payload["reasoning_budget_tokens"] == 256
        assert payload["reasoning_format"] == "deepseek"
        assert payload["chat_template_kwargs"] == {"enable_thinking": True, "reasoning_effort": "low"}


def test_token_budget_uses_server_slots_and_exact_template_wire(service, reviewed, monkeypatch):
    _, pipeline = reviewed
    requests = []
    original_client = httpx.Client

    def respond(request):
        requests.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        if request.url.path == "/slots":
            return httpx.Response(200, json=[{"n_ctx": 172032}])
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "rendered synthetic"})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1, 2, 3]})
        raise AssertionError(request.url.path)

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(pipeline.httpx, "Client", lambda **kwargs: original_client(transport=transport))
    with pipeline._LocalTokenCounter("http://localhost:1234/v1", 2048) as counter:
        assert counter.input_budget() == 32768
        assert counter.count([{"role": "system", "content": "synthetic"}]) == 3
        assert counter.count([{"role": "system", "content": "synthetic"}], actual_wire=True) == 3
    assert [path for _, path, _ in requests] == ["/slots", "/apply-template", "/tokenize", "/apply-template", "/tokenize"]
    assert requests[1][2]["chat_template_kwargs"] == {"enable_thinking": False}
    assert requests[2][2] == {"content": "rendered synthetic", "add_special": True, "with_pieces": False}
    assert requests[3][2]["chat_template_kwargs"] == {"enable_thinking": True, "reasoning_effort": "low"}


def test_final_envelope_overflow_fails_before_model_delivery(service, reviewed, monkeypatch):
    state, pipeline = reviewed
    def overflow(_config, _question, seeds, _expanded, _assets):
        assert len(seeds) == 1
        raise service.AnswerError("DOCMIND_CONTEXT_TOO_LARGE")
    monkeypatch.setattr(pipeline, "_prepare_context", overflow)
    with pytest.raises(service.AnswerError, match="DOCMIND_CONTEXT_TOO_LARGE"):
        asyncio.run(service.answer("tenant", "question", scope={"mode": "all"}))
    assert (state["generate"], state["review"]) == (0, 0)


def test_selection_template_stays_measured_false_while_final_envelopes_use_true(service, reviewed, monkeypatch):
    state, pipeline = reviewed
    modes = []
    class Counter:
        def __init__(self, *_args):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
        def input_budget(self):
            return 10
        def count(self, messages, *, actual_wire=False):
            modes.append(actual_wire)
            return 11 if actual_wire else 1
    monkeypatch.setattr(pipeline, "_LocalTokenCounter", Counter)
    with pytest.raises(service.AnswerError, match="DOCMIND_CONTEXT_TOO_LARGE"):
        state["real_prepare_context"](service.AnswerConfig.from_env(), "q", [sample_chunk()], [sample_chunk()],
                                      pipeline.review.load_assets())
    assert modes == [False, True]
    modes.clear()
    with pytest.raises(service.AnswerError, match="DOCMIND_CONTEXT_TOO_LARGE"):
        state["real_check_review_tokens"](service.AnswerConfig.from_env(), [{"role": "user", "content": "synthetic"}], 10)
    assert modes == [True]


def test_load_context_revalidates_all_sixteen_answer_seeds(service, monkeypatch):
    hits = [sample_chunk(i) for i in range(20)]
    result = {"scope_doc_ids": ["d"], "chunks": hits}
    catalog = types.SimpleNamespace(source="database", dataset_id="kb")
    monkeypatch.setattr(service, "_fresh_catalog", lambda *_: catalog)
    queried = []

    def query(_tenant, _dataset, _doc, *, ids=None, **_kwargs):
        queried.extend(ids or [])
        return [next(hit for hit in hits if hit["chunk_id"] == ids[0]).copy()]

    monkeypatch.setattr(service, "_query_chunks", query)
    seeds, expanded = service._load_context(types.SimpleNamespace(), "tenant", result, {"mode": "all"}, 0, 16)
    assert len(seeds) == len(expanded) == 16
    assert queried == [f"c{i}" for i in range(16)]
    hits[15] = {**hits[15], "doc_id": "forbidden"}
    with pytest.raises(service.AnswerError, match="DOCMIND_CONTEXT_SCOPE_INVALID"):
        service._load_context(types.SimpleNamespace(), "tenant", result, {"mode": "all"}, 0, 16)
