"""Paired search evaluation. Output hashes/metrics only for private documents.

Run inside the existing DocMind environment. Synthetic evaluation writes only
non-sensitive fixture chunks to its own uniquely named Elasticsearch index.
The live-title arm is read-only; title-derived labels test document lookup,
not arbitrary question-answer relevance. Both arms use the real GTE/Jina and
unchanged C128 / document-8 / prefix2400 / Top5 implementation.
"""

import argparse
import asyncio
import faulthandler
import hashlib
import json
import logging
import re
from pathlib import Path
from uuid import uuid4

logging.disable(logging.CRITICAL)
faulthandler.dump_traceback_later(90, repeat=True)


def fixtures():
    rows = [
        (
            "title",
            "휴가신청 처리절차.pdf",
            "직원은 신청서를 작성하여 담당 부서의 승인을 받습니다.",
            "휴가신청 처리절차",
        ),
        (
            "title",
            "출장정산 업무안내.pdf",
            "직원은 신청서를 작성하여 담당 부서의 승인을 받습니다.",
            "출장정산 업무안내",
        ),
        (
            "title",
            "교육등록 운영안내.pdf",
            "직원은 신청서를 작성하여 담당 부서의 승인을 받습니다.",
            "교육등록 운영안내",
        ),
        (
            "title",
            "출입권한 신청안내.pdf",
            "직원은 신청서를 작성하여 담당 부서의 승인을 받습니다.",
            "출입권한 신청안내",
        ),
        (
            "title",
            "장비반출 승인절차.pdf",
            "직원은 신청서를 작성하여 담당 부서의 승인을 받습니다.",
            "장비반출 승인절차",
        ),
        (
            "title",
            "법인카드 발급절차.pdf",
            "직원은 신청서를 작성하여 담당 부서의 승인을 받습니다.",
            "법인카드 발급절차",
        ),
        (
            "korean",
            "생산운영.pdf",
            "스마트팩토리에서는 생산실적을 실시간으로 수집합니다.",
            "스마트 팩토리 생산 실적",
        ),
        (
            "korean",
            "재고운영.pdf",
            "재고관리를 위해 입출고내역을 매일 점검합니다.",
            "재고 관리 입출고 내역",
        ),
        (
            "korean",
            "계정운영.pdf",
            "접근권한을 부여하려면 담당자의 승인을 받아야 합니다.",
            "접근 권한 부여",
        ),
        (
            "korean",
            "정보보호.pdf",
            "개인정보보호를 위해 암호화를 적용합니다.",
            "개인 정보 보호 암호화",
        ),
        (
            "korean",
            "설비운영.pdf",
            "설비점검을 실시하고 고장이력을 기록합니다.",
            "설비 점검 고장 이력",
        ),
        (
            "korean",
            "품질운영.pdf",
            "불량원인을 분석하고 개선대책을 수립합니다.",
            "불량 원인 개선 대책",
        ),
        (
            "product",
            "수집기 개발 1.pdf",
            "C++ 데이터 수집기는 정적 형식 검사를 수행합니다.",
            "C++ 데이터 수집기",
        ),
        (
            "product",
            "수집기 개발 2.pdf",
            "C# 데이터 수집기는 관리형 실행 환경에서 작동합니다.",
            "C# 데이터 수집기",
        ),
        (
            "product",
            "업무시스템 1.pdf",
            "MES 데이터 수집기는 생산 지시와 작업 실적을 연계합니다.",
            "MES 데이터 수집기",
        ),
        (
            "product",
            "업무시스템 2.pdf",
            "ME 데이터 수집기는 측정 장비에서 값을 읽습니다.",
            "ME 데이터 수집기",
        ),
        (
            "product",
            "장치설정 1.pdf",
            "ZX-3000 장치는 TCP 포트 8123을 사용합니다.",
            "ZX-3000 포트",
        ),
        (
            "product",
            "장치설정 2.pdf",
            "ZX-3001 장치는 TCP 포트 9456을 사용합니다.",
            "ZX-3001 포트",
        ),
        (
            "regression",
            "backup.pdf",
            "The backup retention period is thirty days.",
            "backup retention period",
        ),
        (
            "regression",
            "복구안내.pdf",
            "백업 복구는 관리자가 승인한 뒤 수행합니다.",
            "백업 복구 승인",
        ),
        (
            "regression",
            "원격접속.pdf",
            "원격 접속에는 다중 인증이 필요합니다.",
            "원격 접속 다중 인증",
        ),
        (
            "regression",
            "고객지원.pdf",
            "장애 신고는 고객 지원 센터에서 접수합니다.",
            "장애 신고 고객 지원",
        ),
        (
            "regression",
            "데이터보관.pdf",
            "감사 기록의 보관 기간은 365일입니다.",
            "감사 기록 보관 기간",
        ),
        (
            "regression",
            "network.pdf",
            "Network traffic is encrypted using TLS certificates.",
            "network traffic TLS certificates",
        ),
    ]
    docs = [
        {"id": f"fixture-{i:03}", "name": name, "content": content}
        for i, (_, name, content, _) in enumerate(rows)
    ]
    cases = [
        {
            "id": f"synthetic-{i:03}",
            "category": cat,
            "question": question,
            "gold": [docs[i]["id"]],
        }
        for i, (cat, _, _, question) in enumerate(rows)
    ]
    # Unrelated and adjacent-topic distractors make the corpus larger than Top5.
    for i, topic in enumerate(
        ["차량 예약", "회의실 예약", "도서 대출", "식비 정산", "우편 발송", "인사 평가"]
    ):
        docs.append(
            {
                "id": f"distractor-{i:03}",
                "name": topic + ".pdf",
                "content": f"{topic} 업무는 신청서를 작성하여 담당 부서의 승인을 받습니다.",
            }
        )
    return docs, cases


def metric(order, gold, limit):
    rank = next((i for i, doc in enumerate(order[:limit], 1) if doc in gold), None)
    return {"hit": int(rank is not None), "rr": 1 / rank if rank else 0}


def summary(rows):
    result = {}
    for category in ["all", *sorted({r["category"] for r in rows})]:
        group = (
            rows
            if category == "all"
            else [r for r in rows if r["category"] == category]
        )
        result[category] = {
            "n": len(group),
            **{
                name: round(sum(r[name] for r in group) / len(group), 6)
                for name in ["bm25_hit128", "bm25_hit5", "hit5", "mrr5"]
            },
        }
    return result


async def main(args):
    print('{"phase":"imports"}', flush=True)
    import numpy as np
    from api.apps.services import docmind_api_service as service
    from api.db.db_models import DocmindProject
    from api.db.joint_services.tenant_model_service import resolve_model_config
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from api.db.services.llm_service import LLMBundle
    from common import settings
    from common.constants import LLMType
    from common.doc_store.doc_store_base import MatchDenseExpr, OrderByExpr
    from rag.nlp import query, rag_tokenizer

    settings.init_settings()
    faulthandler.cancel_dump_traceback_later()
    print('{"phase":"settings_ready"}', flush=True)
    from rag.llm.embedding_model import OpenAIEmbed

    original_init = OpenAIEmbed.__init__

    def bounded_embedding_client(self, *values, **options):
        original_init(self, *values, **options)
        self.client = self.client.with_options(timeout=30, max_retries=1)

    OpenAIEmbed.__init__ = bounded_embedding_client
    project = next(iter(DocmindProject.select()))
    tenant = project.tenant_id
    catalog = service._load_serving_catalog(tenant)
    _, kb = KnowledgebaseService.get_by_id(catalog.dataset_id)
    model = LLMBundle(
        kb.tenant_id, resolve_model_config(kb.tenant_id, LLMType.EMBEDDING, kb.embd_id)
    )
    reranker = service._rerank_model(catalog)
    es = settings.docStoreConn.es
    candidate = args.arm == "candidate"
    if candidate:
        from rag.nlp.docmind_lexical import supplement_expression, supplemental_fields
    qbuilder = query.FulltextQueryer()
    print('{"phase":"models_bound"}', flush=True)
    checkpoint = Path(args.checkpoint) if args.checkpoint else None
    saved = json.loads(checkpoint.read_text()) if checkpoint and checkpoint.exists() else {}
    if saved.get("catalog_version"):
        assert saved["catalog_version"] == service._catalog_version_id(catalog), "Evaluation source changed"
    rows = saved.get("cases", [])
    completed = {row["id"] for row in rows}

    async def rerank(question, pool):
        if not pool:
            return []
        ordered = sorted(pool, key=lambda c: c.chunk["chunk_id"])
        for attempt in range(4):
            try:
                scores, _ = await asyncio.to_thread(
                    reranker.similarity, question, [c.rerank_text for c in ordered]
                )
                break
            except Exception:
                if attempt == 3:
                    raise RuntimeError("RERANK_EVALUATION_FAILED") from None
                await asyncio.sleep(5 * (attempt + 1))
        scored = sorted(
            zip(ordered, map(float, scores)),
            key=lambda x: (-x[1], -x[0].rrf_score, x[0].chunk["chunk_id"]),
        )
        await asyncio.sleep(args.interval)
        return [c.chunk["doc_id"] for c, _ in scored[:5]]

    def record(case, lexical, order):
        bm = metric(lexical, case["gold"], 128)
        top = metric(order, case["gold"], 5)
        row = {
            "id": case["id"],
            "category": case["category"],
            "bm25_hit128": bm["hit"],
            "bm25_hit5": metric(lexical, case["gold"], 5)["hit"],
            "hit5": top["hit"],
            "mrr5": top["rr"],
        }
        rows.append(row)
        completed.add(case["id"])
        if checkpoint:
            checkpoint.write_text(json.dumps({"catalog_version": service._catalog_version_id(catalog), "cases": rows}), encoding="utf-8")
        print(json.dumps({"progress": len(rows), **row}), flush=True)

    docs, cases = fixtures()
    manifest = list(cases)
    cache_path = Path(args.cache) if args.cache else None
    cache = (
        json.loads(cache_path.read_text()) if cache_path and cache_path.exists() else {}
    )
    fixture_digest = hashlib.sha256(
        json.dumps(docs, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    if cache:
        assert cache["fixture_sha256"] == fixture_digest
    if not args.live_only and any(case["id"] not in completed for case in cases):
        index = "docmind_lexical_eval_" + uuid4().hex
        try:
            mapping = json.loads(Path("/ragflow/conf/mapping.json").read_text())
            mapping["mappings"]["properties"]["q_768_vec"] = {
                "type": "dense_vector",
                "dims": 768,
                "index": True,
                "similarity": "cosine",
            }
            es.indices.create(index=index, **mapping)
            print('{"phase":"synthetic_embedding"}', flush=True)
            if cache:
                vectors = np.asarray(cache["vectors"])
            else:
                titles, _ = await asyncio.to_thread(
                    model.encode, [d["name"] for d in docs]
                )
                bodies, _ = await asyncio.to_thread(
                    model.encode, [d["content"] for d in docs]
                )
                vectors = 0.1 * np.asarray(titles) + 0.9 * np.asarray(bodies)
                cache = {
                    "fixture_sha256": fixture_digest,
                    "vectors": vectors.tolist(),
                    "queries": {},
                }
            if not cache["queries"]:
                query_vectors, _ = await asyncio.to_thread(
                    model.encode, [c["question"] for c in cases]
                )
                cache["queries"] = {
                    c["id"]: list(map(float, vector))
                    for c, vector in zip(cases, query_vectors, strict=True)
                }
            if cache_path:
                cache_path.write_text(json.dumps(cache), encoding="utf-8")
            operations = []
            for d, vector in zip(docs, vectors, strict=True):
                tokens = rag_tokenizer.tokenize(d["content"])
                fields = {
                    "kb_id": "fixture",
                    "doc_id": d["id"],
                    "docnm_kwd": d["name"],
                    "content_with_weight": d["content"],
                    "content_ltks": tokens,
                    "content_sm_ltks": rag_tokenizer.fine_grained_tokenize(tokens),
                    "q_768_vec": vector.tolist(),
                }
                if candidate:
                    title_tokens = rag_tokenizer.tokenize(
                        re.sub(r"\.[^.]+$", "", d["name"])
                    )
                    fields.update(
                        title_tks=title_tokens,
                        title_sm_tks=rag_tokenizer.fine_grained_tokenize(title_tokens),
                    )
                    fields.update(supplemental_fields(d["name"], d["content"]))
                operations.extend(
                    [{"index": {"_index": index, "_id": d["id"]}}, fields]
                )
            assert not es.bulk(operations=operations, refresh="wait_for")["errors"]
            for case in cases:
                if case["id"] in completed:
                    continue
                expression, _ = qbuilder.question(case["question"], min_match=0.3)
                if candidate:
                    expression = supplement_expression(expression, case["question"])
                fields = ["doc_id", "docnm_kwd", "content_with_weight", "kb_id"]
                lexical = settings.docStoreConn.search(
                    fields,
                    [],
                    {},
                    [expression] if expression else [],
                    OrderByExpr(),
                    0,
                    128,
                    index,
                    ["fixture"],
                )
                if case["id"] not in cache["queries"]:
                    vector, _ = await asyncio.to_thread(
                        model.encode_queries, case["question"]
                    )
                    cache["queries"][case["id"]] = list(map(float, vector))
                vector = cache["queries"][case["id"]]
                dense = settings.docStoreConn.search(
                    fields,
                    [],
                    {},
                    [
                        MatchDenseExpr(
                            "q_768_vec",
                            vector,
                            "float",
                            "cosine",
                            2048,
                            {"similarity": 0.0, "num_candidates": 4096},
                        )
                    ],
                    OrderByExpr(),
                    0,
                    128,
                    index,
                    ["fixture"],
                )

                def candidates(response):
                    return [
                        service.Candidate(
                            "fixture",
                            {**h["_source"], "chunk_id": h["_id"]},
                            service._bounded_text(h["_source"]["content_with_weight"]),
                        )
                        for h in response["hits"]["hits"]
                    ]

                bm, dn = candidates(lexical), candidates(dense)
                order = await rerank(case["question"], service._rrf_candidates(bm, dn))
                record(case, [c.chunk["doc_id"] for c in bm], order)
            if cache_path:
                cache_path.write_text(json.dumps(cache), encoding="utf-8")
        finally:
            # Only the exact randomized index created by this invocation.
            assert re.fullmatch(r"docmind_lexical_eval_[0-9a-f]{32}", index)
            es.indices.delete(index=index, ignore_unavailable=True)

    if args.live:
        # Ground truth is fixed by exact normalized filename identity, before any
        # retrieval result is observed. Duplicate names are equally acceptable.
        names = catalog.document_names or {}
        stems = {d: re.sub(r"\.[^.]+$", "", name) for d, name in names.items()}
        selected = sorted(
            stems, key=lambda d: hashlib.sha256(stems[d].encode()).hexdigest()
        )[:12]
        live_cases = []
        for i, doc in enumerate(selected):
            question = stems[doc]
            category = "live_title"
            if i % 2 and re.search("[가-힣]", question):
                question = re.sub(r"(?<=[가-힣])\s+(?=[가-힣])", "", question)
                category = "live_title_spacing"
            gold = [d for d, stem in stems.items() if stem == stems[doc]]
            live_cases.append(
                {
                    "id": f"live-{i:03}",
                    "question": question,
                    "gold": gold,
                    "category": category,
                }
            )
        manifest.extend(live_cases)
        for case in live_cases:
            if case["id"] in completed:
                continue
            resolved = service._resolve_scope(catalog, {"mode": "all"})
            lexical = await service._lane_candidates(
                tenant, case["question"], catalog, resolved, candidate_mode="bm25"
            )
            for attempt in range(4):
                try:
                    result = await service.search(tenant, case["question"], scope={"mode": "all"})
                    break
                except RuntimeError as error:
                    if str(error) not in {"DOCMIND_RERANK_FAILED", "DOCMIND_RERANK_TIMEOUT", "DOCMIND_RECALL_TIMEOUT"} or attempt == 3:
                        raise RuntimeError("LIVE_EVALUATION_PROVIDER_FAILED") from None
                    await asyncio.sleep(10 * (attempt + 1))
            record(
                case,
                [c.chunk["doc_id"] for c in lexical],
                [c["doc_id"] for c in result["chunks"]],
            )
            await asyncio.sleep(args.interval)
    print(
        "EVALUATION_JSON "
        + json.dumps(
            {
                "arm": args.arm,
                "manifest_sha256": hashlib.sha256(
                    json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest(),
                "catalog_version": service._catalog_version_id(catalog),
                "unique_live_questions": len({c["question"] for c in manifest if c["category"].startswith("live_")}),
                "summary": summary(rows),
                "cases": rows,
                "synthetic_vectors_sha256": hashlib.sha256(
                    json.dumps(cache, sort_keys=True).encode()
                ).hexdigest(),
                "synthetic_index_removed": not args.live_only,
                "contract": {
                    "lane": 128,
                    "candidates": 128,
                    "per_document": 8,
                    "prefix": 2400,
                    "top": 5,
                },
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=["baseline", "candidate"], required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--live-only", action="store_true")
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--cache")
    parser.add_argument("--checkpoint")
    asyncio.run(main(parser.parse_args()))
