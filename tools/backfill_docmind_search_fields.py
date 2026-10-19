"""Add lexical fields to existing active chunks without reparsing/reembedding.

The rollback journal contains private derived text and MUST be outside Git.
Updates use Elasticsearch sequence/primary-term guards; a concurrent source
change is never overwritten. A journal is append-only and records the exact
acknowledged update version needed for a guarded rollback.
"""

import argparse
import hashlib
import json
import logging
import os
import re
from pathlib import Path

logging.disable(logging.CRITICAL)


FIELDS = (
    "title_tks",
    "title_sm_tks",
    "lexical_title_ko_ltks",
    "lexical_content_ko_ltks",
    "lexical_title_exact_ltks",
    "lexical_content_exact_ltks",
)


def source_digest(source):
    return hashlib.sha256(
        json.dumps(
            {k: v for k, v in source.items() if k not in FIELDS},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def main(args):
    from api.apps.services.docmind_api_service import _load_serving_catalog
    from api.db.db_models import DocmindProject
    from common.config_utils import get_base_config
    from elasticsearch import Elasticsearch
    from elasticsearch.helpers import scan
    from rag.nlp import rag_tokenizer
    from rag.nlp.docmind_lexical import supplemental_fields

    cfg = get_base_config("es", {})
    es = Elasticsearch(
        cfg["hosts"].split(","),
        basic_auth=(cfg["username"], cfg["password"]) if "username" in cfg and "password" in cfg else None,
        verify_certs=cfg.get("verify_certs", False),
        request_timeout=30,
    )
    journal = Path(args.journal)
    assert ".local" in journal.parts or "private" in journal.as_posix(), (
        "Journal must be private"
    )
    counts = {"scanned": 0, "changed": 0, "unchanged": 0, "conflicts": 0}
    if args.verify:
        applied = [
            json.loads(line)
            for line in journal.read_text().splitlines()
            if json.loads(line).get("kind") == "applied"
        ]
        for offset in range(0, len(applied), 200):
            batch = applied[offset : offset + 200]
            result = es.mget(
                docs=[{"_index": row["index"], "_id": row["id"]} for row in batch]
            )
            for old, current in zip(batch, result["docs"], strict=True):
                assert (
                    current.get("found")
                    and source_digest(current["_source"]) == old["source_sha256"]
                ), "Protected chunk fields changed"
                assert all(k in current["_source"] for k in FIELDS), (
                    "Missing lexical field"
                )
                counts["scanned"] += 1
        print(
            json.dumps(
                {
                    "protected_fields_preserved": True,
                    "verified_chunks": counts["scanned"],
                }
            )
        )
        return
    if args.rollback:
        receipts = [json.loads(line) for line in journal.read_text().splitlines()]
        for row in reversed(receipts):
            if row.get("kind") != "applied":
                continue
            try:
                es.update(
                    index=row["index"],
                    id=row["id"],
                    if_seq_no=row["seq_no"],
                    if_primary_term=row["primary_term"],
                    script={
                        "lang": "painless",
                        "source": "for (def k : params.names) {ctx._source.remove(k);} for (def e : params.old.entrySet()) {ctx._source[e.getKey()]=e.getValue();}",
                        "params": {"names": list(FIELDS), "old": row["old"]},
                    },
                )
                counts["changed"] += 1
            except Exception as error:
                if getattr(error, "status_code", None) == 409:
                    counts["conflicts"] += 1
                else:
                    raise RuntimeError("BACKFILL_ROLLBACK_FAILED") from None
        print(json.dumps(counts))
        return
    if args.apply:
        assert not journal.exists(), "Keep the previous journal; use a fresh path"
        journal.parent.mkdir(parents=True, exist_ok=True)
    out = journal.open("x", encoding="utf8") if args.apply else None
    touched = set()
    try:
        for project in DocmindProject.select():
            catalog = _load_serving_catalog(project.tenant_id)
            chunk_sets = catalog.document_chunk_set_ids or {}
            if not chunk_sets:
                continue
            condition = {
                "bool": {
                    "filter": [
                        {"term": {"kb_id": catalog.dataset_id}},
                        {"terms": {"doc_id": list(chunk_sets)}},
                        {"terms": {"chunk_set_id": list(chunk_sets.values())}},
                    ],
                    "must_not": [
                        {"range": {"available_int": {"lt": 1}}},
                        {"exists": {"field": "compile_kwd"}},
                    ],
                }
            }
            for hit in scan(
                es,
                index="ragflow_" + project.tenant_id,
                query={"query": condition, "seq_no_primary_term": True},
                size=200,
            ):
                source = hit["_source"]
                counts["scanned"] += 1
                name = (catalog.document_names or {}).get(
                    source["doc_id"], source.get("docnm_kwd", "")
                )
                title = rag_tokenizer.tokenize(re.sub(r"\.[a-zA-Z0-9]{1,8}$", "", name))
                fields = {
                    "title_tks": title,
                    "title_sm_tks": rag_tokenizer.fine_grained_tokenize(title),
                    **supplemental_fields(name, source.get("content_with_weight", "")),
                }
                if all(source.get(k) == v for k, v in fields.items()):
                    counts["unchanged"] += 1
                    continue
                if not args.apply:
                    counts["changed"] += 1
                    continue
                old = {k: source[k] for k in FIELDS if k in source}
                receipt = {
                    "index": hit["_index"],
                    "id": hit["_id"],
                    "old": old,
                    "source_sha256": source_digest(source),
                }
                out.write(json.dumps({"kind": "intent", **receipt}) + "\n")
                out.flush()
                os.fsync(out.fileno())
                try:
                    result = es.update(
                        index=hit["_index"],
                        id=hit["_id"],
                        doc=fields,
                        if_seq_no=hit["_seq_no"],
                        if_primary_term=hit["_primary_term"],
                    )
                except Exception as error:
                    if getattr(error, "status_code", None) == 409:
                        counts["conflicts"] += 1
                        continue
                    raise RuntimeError("BACKFILL_UPDATE_FAILED") from None
                out.write(
                    json.dumps(
                        {
                            "kind": "applied",
                            **receipt,
                            "seq_no": result["_seq_no"],
                            "primary_term": result["_primary_term"],
                        }
                    )
                    + "\n"
                )
                out.flush()
                counts["changed"] += 1
                touched.add(hit["_index"])
                if counts["changed"] % 1000 == 0:
                    print(json.dumps({"progress": counts["changed"]}), flush=True)
        for index in touched:
            es.indices.refresh(index=index)
        print("BACKFILL_JSON " + json.dumps(counts), flush=True)
    finally:
        if out:
            out.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--verify", action="store_true")
    main(parser.parse_args())
