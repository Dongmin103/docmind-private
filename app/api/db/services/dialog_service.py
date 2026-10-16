#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import html
import logging
import re
import time
from copy import deepcopy

logger = logging.getLogger(__name__)
from datetime import datetime, timezone
from timeit import default_timer as timer
from langfuse import Langfuse, propagate_attributes
from peewee import fn
from api.db.services.file_service import FileService
from common.constants import LLMType, StatusEnum
from api.db.db_models import DB, Dialog
from api.db.services.common_service import CommonService
from api.db.services.doc_metadata_service import DocMetadataService
from api.db.services.knowledgebase_service import KnowledgebaseService, validate_dataset_embedding_models
from api.db.services.langfuse_service import TenantLangfuseService
from api.db.services.llm_service import LLMBundle, resolve_llm_setting
from common.metadata_utils import apply_meta_data_filter
from api.utils.reference_metadata_utils import (
    enrich_chunks_with_document_metadata,
    resolve_reference_metadata_preferences,
)
from api.db.joint_services.tenant_model_service import get_tenant_default_model_by_type, resolve_model_config, resolve_model_type, get_model_config_by_id
from common.time_utils import current_timestamp, datetime_format
from common.text_utils import normalize_arabic_digits
from rag.app.tag import label_question
from rag.prompts.generator import chunks_format, citation_prompt, cross_languages, full_question, kb_prompt, keyword_extraction, message_fit_in, PROMPT_JINJA_ENV, ASK_SUMMARY
from common.token_utils import num_tokens_from_string
from common import settings


def _chunk_kb_id_for_doc(row_dict, kb_ids, doc_id):
    if len(kb_ids or []) == 1:
        return kb_ids[0]
    return row_dict.get("kb_id") or row_dict.get("kb_id_kwd")


async def _hydrate_chunk_vectors(retriever, chunks, tenant_ids, kb_ids):
    """
    Citation prep: on the ES backend the main retrieval call deliberately
    skips fetching the chunk embedding. insert_citations needs it, so we
    pull the vectors for just the candidate chunks right before computing
    answer-vs-chunk similarity. Chunks without an ES chunk_id (e.g. web
    search results) keep whatever placeholder they were given. Other
    results without chunk IDs retain their placeholder vectors.
    """
    if not chunks:
        return
    dim = 0
    for ck in chunks:
        v = ck.get("vector")
        if isinstance(v, list) and v:
            dim = len(v)
            break
    if not dim:
        return
    # Skip chunks that already have a non-zero vector (e.g. parent chunks
    # produced by retrieval_by_children copy the child vector inline).
    chunk_ids = []
    for ck in chunks:
        cid = ck.get("chunk_id")
        if not cid:
            continue
        v = ck.get("vector") or []
        if any(x for x in v):
            continue
        chunk_ids.append(cid)
    if not chunk_ids:
        return
    try:
        vectors = await retriever.fetch_chunk_vectors(chunk_ids, tenant_ids, kb_ids, dim)
    except Exception as e:  # noqa: BLE001 - degrade gracefully on hydrate failure
        logger.warning("fetch_chunk_vectors failed; citations will use placeholders: %s", e)
        return
    if not vectors:
        return
    for ck in chunks:
        cid = ck.get("chunk_id")
        if cid and cid in vectors:
            ck["vector"] = vectors[cid]


def _resolve_reference_metadata(config, request_payload=None):
    return resolve_reference_metadata_preferences(request_payload or {}, config)


def _enrich_chunks_with_document_metadata(chunks, metadata_fields=None):
    enrich_chunks_with_document_metadata(chunks, metadata_fields)


class DialogService(CommonService):
    model = Dialog

    @classmethod
    def save(cls, **kwargs):
        """Save a new record to database.

        This method creates a new record in the database with the provided field values,
        forcing an insert operation rather than an update.

        Args:
            **kwargs: Record field values as keyword arguments.

        Returns:
            Model instance: The created record object.
        """
        sample_obj = cls.model(**kwargs).save(force_insert=True)
        return sample_obj

    @classmethod
    def update_many_by_id(cls, data_list):
        """Update multiple records by their IDs.

        This method updates multiple records in the database, identified by their IDs.
        It automatically updates the update_time and update_date fields for each record.

        Args:
            data_list (list): List of dictionaries containing record data to update.
                             Each dictionary must include an 'id' field.
        """
        with DB.atomic():
            for data in data_list:
                data["update_time"] = current_timestamp()
                data["update_date"] = datetime_format(datetime.now())
                cls.model.update(data).where(cls.model.id == data["id"]).execute()

    @classmethod
    @DB.connection_context()
    def get_list(cls, tenant_id, page_number, items_per_page, orderby, desc, id, name):
        chats = cls.model.select()
        if id:
            chats = chats.where(cls.model.id == id)
        if name:
            chats = chats.where(cls.model.name == name)
        chats = chats.where((cls.model.tenant_id == tenant_id) & (cls.model.status == StatusEnum.VALID.value))
        if desc:
            chats = chats.order_by(cls.model.getter_by(orderby).desc())
        else:
            chats = chats.order_by(cls.model.getter_by(orderby).asc())

        chats = chats.paginate(page_number, items_per_page)

        return list(chats.dicts())

    @classmethod
    @DB.connection_context()
    def get_by_tenant_ids(
        cls,
        joined_tenant_ids,
        user_id,
        page_number,
        items_per_page,
        orderby,
        desc,
        keywords,
        id=None,
        name=None,
    ):
        from api.db.db_models import User

        fields = [
            cls.model.id,
            cls.model.tenant_id,
            cls.model.name,
            cls.model.description,
            cls.model.language,
            cls.model.llm_id,
            cls.model.llm_setting,
            cls.model.prompt_type,
            cls.model.prompt_config,
            cls.model.similarity_threshold,
            cls.model.vector_similarity_weight,
            cls.model.top_n,
            cls.model.rerank_candidates_count,
            cls.model.top_k,
            cls.model.do_refer,
            cls.model.rerank_id,
            cls.model.kb_ids,
            cls.model.icon,
            cls.model.status,
            User.nickname,
            User.avatar.alias("tenant_avatar"),
            cls.model.update_time,
            cls.model.create_time,
        ]
        dialogs = (
            cls.model.select(*fields)
            .join(User, on=(cls.model.tenant_id == User.id))
            .where(
                (cls.model.tenant_id.in_(joined_tenant_ids) | (cls.model.tenant_id == user_id)) & (cls.model.status == StatusEnum.VALID.value),
            )
        )
        if id:
            dialogs = dialogs.where(cls.model.id == id)
        if name:
            dialogs = dialogs.where(cls.model.name == name)
        if keywords:
            dialogs = dialogs.where(fn.LOWER(cls.model.name).contains(keywords.lower()))
        if desc:
            dialogs = dialogs.order_by(cls.model.getter_by(orderby).desc())
        else:
            dialogs = dialogs.order_by(cls.model.getter_by(orderby).asc())

        count = dialogs.count()

        if page_number and items_per_page:
            dialogs = dialogs.paginate(page_number, items_per_page)

        return list(dialogs.dicts()), count

    @classmethod
    @DB.connection_context()
    def get_all_dialogs_by_tenant_id(cls, tenant_id):
        fields = [cls.model.id]
        dialogs = cls.model.select(*fields).where(cls.model.tenant_id == tenant_id)
        dialogs = dialogs.order_by(cls.model.create_time.asc())
        offset, limit = 0, 100
        res = []
        while True:
            d_batch = dialogs.offset(offset).limit(limit)
            _temp = list(d_batch.dicts())
            if not _temp:
                break
            res.extend(_temp)
            offset += limit
        return res

    @classmethod
    @DB.connection_context()
    def get_null_tenant_llm_id_row(cls):
        fields = [cls.model.id, cls.model.tenant_id, cls.model.llm_id]
        objs = cls.model.select(*fields).where(cls.model.tenant_llm_id.is_null())
        return list(objs)

    @classmethod
    @DB.connection_context()
    def get_null_tenant_rerank_id_row(cls):
        fields = [cls.model.id, cls.model.tenant_id, cls.model.rerank_id]
        objs = cls.model.select(*fields).where(cls.model.tenant_rerank_id.is_null())
        return list(objs)


async def async_chat_solo(dialog, messages, stream=True, session_id=None):
    if dialog.llm_id:
        if dialog.tenant_llm_id:
            try:
                llm_types = resolve_model_type(dialog.tenant_id, dialog.llm_id)
                if "chat" in llm_types:
                    model_config = get_model_config_by_id(dialog.tenant_id, LLMType.CHAT, dialog.tenant_llm_id)
                else:
                    model_config = resolve_model_config(dialog.tenant_id, LLMType.VISION, dialog.llm_id)
            except LookupError:
                llm_types = resolve_model_type(dialog.tenant_id, dialog.llm_id)
                if "chat" in llm_types:
                    model_config = resolve_model_config(dialog.tenant_id, LLMType.CHAT, dialog.llm_id)
                else:
                    model_config = resolve_model_config(dialog.tenant_id, LLMType.VISION, dialog.llm_id)
        else:
            llm_types = resolve_model_type(dialog.tenant_id, dialog.llm_id)
            if "chat" in llm_types:
                model_config = resolve_model_config(dialog.tenant_id, LLMType.CHAT, dialog.llm_id)
            else:
                model_config = resolve_model_config(dialog.tenant_id, LLMType.VISION, dialog.llm_id)
    else:
        model_config = get_tenant_default_model_by_type(dialog.tenant_id, LLMType.CHAT)

    chat_mdl = LLMBundle(dialog.tenant_id, model_config, langfuse_session_id=session_id)
    factory = model_config.get("llm_factory", "") if model_config else ""

    text_attachments_content, image_attachments, image_files = get_files_content(messages[-1], model_config["model_type"])

    prompt_config = dialog.prompt_config


    msg = [{"role": m["role"], "content": re.sub(r"##\d+\$\$", "", m["content"])} for m in messages if m["role"] != "system"]
    if text_attachments_content and msg:
        msg[-1]["content"] += text_attachments_content
    if model_config["model_type"] == "chat" and image_attachments:
        convert_last_user_msg_to_multimodal(msg, image_attachments, factory)
    sys_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    system_prompt = prompt_config.get("system", "").replace("{date}", sys_date)
    if stream:
        if model_config["model_type"] == "chat":
            stream_iter = chat_mdl.async_chat_streamly_delta(system_prompt, msg, dialog.llm_setting)
        else:
            stream_iter = chat_mdl.async_chat_streamly_delta(system_prompt, msg, dialog.llm_setting, images=image_files)
        async for kind, value, state in _stream_with_think_delta(stream_iter):
            if kind == "marker":
                flags = {"start_to_think": True} if value == "<think>" else {"end_to_think": True}
                yield {"answer": "", "reference": {}, "prompt": "", "created_at": time.time(), "final": False, **flags}
                continue
            yield {"answer": value, "reference": {}, "prompt": "", "created_at": time.time(), "final": False}
    else:
        if model_config["model_type"] == "chat":
            answer = await chat_mdl.async_chat(system_prompt, msg, dialog.llm_setting)
        else:
            answer = await chat_mdl.async_chat(system_prompt, msg, dialog.llm_setting, images=image_files)
        user_content = msg[-1].get("content", "[content not available]")
        logging.debug("User: {}|Assistant: {}".format(user_content, answer))
        yield {"answer": answer, "reference": {}, "prompt": "", "created_at": time.time()}


def get_models(dialog, trace_context=None, langfuse_session_id=None):
    embd_mdl, chat_mdl, rerank_mdl = None, None, None
    kbs = KnowledgebaseService.get_by_ids(dialog.kb_ids)
    err = validate_dataset_embedding_models(kbs)
    if err:
        raise Exception(err)

    if kbs and kbs[0].embd_id:
        embd_owner_tenant_id = kbs[0].tenant_id
        embd_model_config = resolve_model_config(embd_owner_tenant_id, LLMType.EMBEDDING, kbs[0].embd_id)
        embd_mdl = LLMBundle(embd_owner_tenant_id, embd_model_config, trace_context=trace_context, langfuse_session_id=langfuse_session_id)
        if not embd_mdl:
            raise LookupError("Embedding model(%s) not found" % kbs[0].embd_id)

    if dialog.llm_id:
        if dialog.tenant_llm_id:
            try:
                chat_model_config = get_model_config_by_id(dialog.tenant_id, LLMType.CHAT, dialog.tenant_llm_id)
            except LookupError:
                chat_model_config = resolve_model_config(dialog.tenant_id, LLMType.CHAT, dialog.llm_id)
        else:
            chat_model_config = resolve_model_config(dialog.tenant_id, LLMType.CHAT, dialog.llm_id)
    else:
        chat_model_config = get_tenant_default_model_by_type(dialog.tenant_id, LLMType.CHAT)

    chat_mdl = LLMBundle(dialog.tenant_id, chat_model_config, trace_context=trace_context, langfuse_session_id=langfuse_session_id)

    if dialog.rerank_id:
        if dialog.tenant_rerank_id:
            try:
                rerank_model_config = get_model_config_by_id(dialog.tenant_id, LLMType.RERANK, dialog.tenant_rerank_id)
            except LookupError:
                rerank_model_config = resolve_model_config(dialog.tenant_id, LLMType.RERANK, dialog.rerank_id)
        else:
            rerank_model_config = resolve_model_config(dialog.tenant_id, LLMType.RERANK, dialog.rerank_id)
        rerank_mdl = LLMBundle(dialog.tenant_id, rerank_model_config, trace_context=trace_context, langfuse_session_id=langfuse_session_id)


    return kbs, embd_mdl, rerank_mdl, chat_mdl


def get_files_content(last_message, model_type):
    text_attachments_content = ""
    image_attachments = []
    image_files = []
    if "files" in last_message:
        if model_type == "chat":
            text_attachments, image_attachments = split_file_attachments(last_message["files"])
        else:
            text_attachments, image_files = split_file_attachments(last_message["files"], raw=True)
        text_attachments_content = "\n\n".join(text_attachments)
    return text_attachments_content, image_attachments, image_files


def split_file_attachments(files: list[dict] | None, raw: bool = False) -> tuple[list[str], list[str] | list[dict]]:
    if not files:
        return [], []

    text_attachments = []
    if raw:
        file_contents, image_files = FileService.get_files(files, raw=True)
        for content in file_contents:
            if not isinstance(content, str):
                content = str(content)
            text_attachments.append(content)
        return text_attachments, image_files

    image_attachments = []
    for content in FileService.get_files(files, raw=False):
        if not isinstance(content, str):
            content = str(content)
        if content.strip().startswith("data:"):
            image_attachments.append(content.strip())
            continue
        text_attachments.append(content)
    return text_attachments, image_attachments


_DATA_URI_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<b64>[A-Za-z0-9+/=\s]+)$")


def _parse_data_uri_or_b64(s: str, default_mime: str = "image/png") -> tuple[str, str]:
    s = (s or "").strip()
    match = _DATA_URI_RE.match(s)
    if match:
        mime = match.group("mime").strip()
        b64 = match.group("b64").strip()
        return mime, b64
    return default_mime, s


def _normalize_text_from_content(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for blk in content:
            if isinstance(blk, dict):
                if blk.get("type") in {"text", "input_text"}:
                    txt = blk.get("text")
                    if txt:
                        texts.append(str(txt))
                elif "text" in blk and isinstance(blk.get("text"), (str, int, float)):
                    texts.append(str(blk["text"]))
        return "\n".join(texts).strip()
    return str(content)


def convert_last_user_msg_to_multimodal(msg: list[dict], image_data_uris: list[str], factory: str) -> None:
    if not msg or not image_data_uris:
        return


    for idx in range(len(msg) - 1, -1, -1):
        if msg[idx].get("role") != "user":
            continue

        original_content = msg[idx].get("content", "")
        text = _normalize_text_from_content(original_content)

        multimodal_content = []
        if isinstance(original_content, list):
            multimodal_content = deepcopy(original_content)
        else:
            text_content = "" if original_content is None else str(original_content)
            if text_content:
                multimodal_content.append({"type": "text", "text": text_content})

        for data_uri in image_data_uris:
            image_url = data_uri
            if not isinstance(image_url, str):
                image_url = str(image_url)
            if not image_url.startswith("data:"):
                image_url = f"data:image/png;base64,{image_url}"
            multimodal_content.append({"type": "image_url", "image_url": {"url": image_url}})

        msg[idx]["content"] = multimodal_content
        return


# Keys the chat-completions message schema defines. Stored messages also carry
# RAGFlow bookkeeping such as id, created_at and doc_ids, plus the conversationId
# the web client stamps on every turn, and strict providers reject those.
LLM_MESSAGE_FIELDS = frozenset({"role", "content", "name", "tool_calls", "tool_call_id", "function_call", "refusal", "audio"})

BAD_CITATION_PATTERNS = [
    re.compile(r"\(\s*ID\s*[: ]*\s*(\d+)\s*\)"),  # (ID: 12)
    re.compile(r"\[\s*ID\s*[: ]*\s*(\d+)\s*\]"),  # [ID: 12]
    re.compile(r"【\s*ID\s*[: ]*\s*(\d+)\s*】"),  # 【ID: 12】
    re.compile(r"ref\s*(\d+)", flags=re.IGNORECASE),  # ref12、REF 12
]
CITATION_MARKER_PATTERN = re.compile(r"\[(?:ID:)?([0-9\u0660-\u0669\u06F0-\u06F9]+)\]")


def repair_bad_citation_formats(answer: str, kbinfos: dict, idx: set):
    max_index = len(kbinfos["chunks"])
    normalized_answer = normalize_arabic_digits(answer) or ""

    def safe_add(i):
        if 0 <= i < max_index:
            idx.add(i)
            return True
        return False

    def find_and_replace(pattern, group_index=1, repl=lambda digits: f"ID:{digits}"):
        nonlocal answer
        nonlocal normalized_answer

        matches = list(pattern.finditer(normalized_answer))
        if not matches:
            return

        parts = []
        last_idx = 0
        for match in matches:
            parts.append(answer[last_idx : match.start()])
            try:
                i = int(match.group(group_index))
            except Exception:
                parts.append(answer[match.start() : match.end()])
                last_idx = match.end()
                continue

            if safe_add(i):
                digit_start, digit_end = match.span(group_index)
                digits_original = answer[digit_start:digit_end]
                parts.append(f"[{repl(digits_original)}]")
            else:
                parts.append(answer[match.start() : match.end()])
            last_idx = match.end()

        parts.append(answer[last_idx:])
        answer = "".join(parts)
        normalized_answer = normalize_arabic_digits(answer) or ""

    for pattern in BAD_CITATION_PATTERNS:
        find_and_replace(pattern)

    return answer, idx


async def async_chat(dialog, messages, stream=True, **kwargs):
    logging.debug("Begin async_chat")
    assert messages[-1]["role"] == "user", "The last content of this conversation is not from user."
    session_id = kwargs.get("session_id")
    if not dialog.kb_ids:
        async for ans in async_chat_solo(dialog, messages, stream, session_id=session_id):
            yield ans
        return

    chat_start_ts = timer()
    if dialog.llm_id:
        if dialog.tenant_llm_id:
            try:
                llm_types = resolve_model_type(dialog.tenant_id, dialog.llm_id)
                if "chat" in llm_types:
                    llm_model_config = get_model_config_by_id(dialog.tenant_id, LLMType.CHAT, dialog.tenant_llm_id)
                else:
                    llm_model_config = resolve_model_config(dialog.tenant_id, LLMType.VISION, dialog.llm_id)
            except LookupError:
                llm_types = resolve_model_type(dialog.tenant_id, dialog.llm_id)
                if "chat" in llm_types:
                    llm_model_config = resolve_model_config(dialog.tenant_id, LLMType.CHAT, dialog.llm_id)
                else:
                    llm_model_config = resolve_model_config(dialog.tenant_id, LLMType.VISION, dialog.llm_id)
        else:
            llm_types = resolve_model_type(dialog.tenant_id, dialog.llm_id)
            if "chat" in llm_types:
                llm_model_config = resolve_model_config(dialog.tenant_id, LLMType.CHAT, dialog.llm_id)
            else:
                llm_model_config = resolve_model_config(dialog.tenant_id, LLMType.VISION, dialog.llm_id)
    else:
        llm_model_config = get_tenant_default_model_by_type(dialog.tenant_id, LLMType.CHAT)

    factory = llm_model_config.get("llm_factory", "") if llm_model_config else ""
    max_tokens = llm_model_config.get("max_tokens") or 8192

    check_llm_ts = timer()

    langfuse_tracer = None
    langfuse_generation = None
    trace_context = {}
    langfuse_keys = TenantLangfuseService.filter_by_tenant(tenant_id=dialog.tenant_id)
    if langfuse_keys:
        langfuse = Langfuse(public_key=langfuse_keys.public_key, secret_key=langfuse_keys.secret_key, host=langfuse_keys.host)
        try:
            if langfuse.auth_check():
                langfuse_tracer = langfuse
                trace_id = langfuse_tracer.create_trace_id()
                trace_context = {"trace_id": trace_id}
        except Exception:
            # Skip langfuse tracing if connection fails
            pass

    check_langfuse_tracer_ts = timer()
    kbs, embd_mdl, rerank_mdl, chat_mdl = get_models(dialog, trace_context=trace_context, langfuse_session_id=session_id)
    bind_models_ts = timer()

    retriever = settings.retriever
    questions = [m["content"] for m in messages if m["role"] == "user"][-3:]

    # Get scoped_doc_ids
    scoped_doc_ids = None
    if "doc_ids" in kwargs:
        scoped_doc_ids = [doc_id for doc_id in kwargs["doc_ids"].split(",") if doc_id]
    if "doc_ids" in messages[-1]:
        scoped_doc_ids = [doc_id for doc_id in messages[-1]["doc_ids"] if doc_id]
    if dialog.meta_data_filter:
        scoped_doc_ids = await apply_meta_data_filter(
            dialog.meta_data_filter,
            None,
            questions[-1],
            chat_mdl,
            scoped_doc_ids,
            kb_ids=dialog.kb_ids,
            metas_loader=lambda: DocMetadataService.get_flatted_meta_by_kbs(dialog.kb_ids),
        )

    # Get chat attachments
    text_attachments_content, image_attachments, image_files = get_files_content(messages[-1], llm_model_config["model_type"])

    prompt_config = dialog.prompt_config
    rerank_candidates_count = getattr(dialog, "rerank_candidates_count", 64)
    include_reference_metadata, metadata_fields = _resolve_reference_metadata(prompt_config, request_payload=kwargs)
    param_keys = [p["key"] for p in prompt_config.get("parameters", [])]
    if dialog.kb_ids and "knowledge" not in param_keys and "{knowledge}" in prompt_config.get("system", ""):
        logging.warning("prompt_config['parameters'] is missing 'knowledge' entry despite kb_ids being set; auto-fixing.")
        prompt_config.setdefault("parameters", []).append({"key": "knowledge", "optional": False})
        param_keys.append("knowledge")
    logging.debug(f"scoped_doc_ids={scoped_doc_ids}, param_keys={param_keys}, embd_mdl={embd_mdl}")

    sys_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    kwargs["date"] = sys_date
    for p in prompt_config.get("parameters", []):
        if p["key"] == "knowledge":
            continue
        if p["key"] not in kwargs and not p["optional"]:
            raise KeyError("Miss parameter: " + p["key"])
        if p["key"] not in kwargs:
            prompt_config["system"] = prompt_config["system"].replace("{%s}" % p["key"], " ")

    if len(questions) > 1 and prompt_config.get("refine_multiturn"):
        questions = [await full_question(dialog.tenant_id, dialog.llm_id, messages)]
    else:
        questions = questions[-1:]

    if prompt_config.get("cross_languages"):
        questions = [await cross_languages(dialog.tenant_id, dialog.llm_id, questions[0], prompt_config["cross_languages"])]

    if prompt_config.get("keyword", False):
        questions[-1] = questions[-1] + "," + await keyword_extraction(chat_mdl, questions[-1])
    refine_question_ts = timer()

    thought = ""
    kbinfos = {"total": 0, "chunks": [], "doc_aggs": []}
    knowledges = []

    if "knowledge" in param_keys:
        logging.debug("Proceeding with retrieval")
        tenant_ids = list(set([kb.tenant_id for kb in kbs]))
        knowledges = []
        if embd_mdl:
            kbinfos = await retriever.retrieval(
                " ".join(questions),
                embd_mdl,
                tenant_ids,
                dialog.kb_ids,
                1,
                dialog.top_n,
                dialog.similarity_threshold,
                dialog.vector_similarity_weight,
                doc_ids=scoped_doc_ids,
                knn_top_k=dialog.top_k,
                aggs=True,
                rerank_mdl=rerank_mdl,
                rank_feature=label_question(" ".join(questions), kbs),
                rerank_candidates_count=rerank_candidates_count,
            )
            if prompt_config.get("toc_enhance"):
                cks = await retriever.retrieval_by_toc(" ".join(questions), kbinfos["chunks"], tenant_ids, chat_mdl, dialog.top_n)
                if cks:
                    kbinfos["chunks"] = cks
            kbinfos["chunks"] = retriever.retrieval_by_children(kbinfos["chunks"], tenant_ids)
    if include_reference_metadata:
        logging.debug(
            "reference_metadata enrichment enabled for async_chat: chunk_count=%d metadata_fields=%s",
            len(kbinfos.get("chunks", [])),
            metadata_fields,
        )
        _enrich_chunks_with_document_metadata(kbinfos.get("chunks", []), metadata_fields)

    knowledges = kb_prompt(kbinfos, max_tokens)
    logging.debug("{}->{}".format(" ".join(questions), "\n->".join(knowledges)))

    retrieval_ts = timer()
    if not knowledges and prompt_config.get("empty_response"):
        empty_res = prompt_config["empty_response"]
        # HTML-escape for frontend display so DOMPurify does not strip
        # unknown tags (e.g. <abc> → &lt;abc&gt;), which would otherwise
        # leave the content blank and stall the UI on "Searching…".
        escaped_answer = html.escape(empty_res)
        yield {"answer": escaped_answer, "reference": {}, "prompt": "", "final": False}
        yield {"answer": escaped_answer, "reference": kbinfos, "prompt": "\n\n### Query:\n%s" % " ".join(questions), "final": True}
        return

    # Only overwrite kwargs["knowledge"] when retrieval produced something;
    # otherwise preserve any caller-supplied value.
    knowledge_text = "\n\n------\n\n".join(knowledges)
    if knowledge_text:
        kwargs["knowledge"] = "\n------\n" + knowledge_text
    else:
        kwargs.setdefault("knowledge", "")
    gen_conf = dialog.llm_setting

    system_content = prompt_config["system"].format(**kwargs)
    # If knowledge was retrieved but the template has no {knowledge}
    # placeholder, auto-append it so the LLM still sees the context.
    if knowledges and "{knowledge}" not in prompt_config.get("system", ""):
        system_content += kwargs["knowledge"]
    msg = [{"role": "system", "content": system_content}]
    prompt4citation = ""
    if knowledges and (prompt_config.get("quote", True) and kwargs.get("quote", True)):
        prompt4citation = citation_prompt()
    msg.extend([{"role": m["role"], "content": re.sub(r"##\d+\$\$", "", m["content"])} for m in messages if m["role"] != "system"])
    if text_attachments_content and msg:
        msg[-1]["content"] += text_attachments_content
    used_token_count, msg = message_fit_in(msg, int(max_tokens * 0.95))
    if llm_model_config["model_type"] == "chat" and image_attachments:
        convert_last_user_msg_to_multimodal(msg, image_attachments, factory)
    assert len(msg) >= 2, f"message_fit_in has bug: {msg}"
    prompt = msg[0]["content"]

    if "max_tokens" in gen_conf:
        gen_conf["max_tokens"] = min(gen_conf["max_tokens"], max_tokens - used_token_count)

    async def decorate_answer(answer):
        nonlocal embd_mdl, prompt_config, knowledges, kwargs, kbinfos, prompt, retrieval_ts, questions, langfuse_generation

        refs = []
        ans = answer.split("</think>")
        think = ""
        if len(ans) == 2:
            think = ans[0] + "</think>"
            answer = ans[1]

        if knowledges and (prompt_config.get("quote", True) and kwargs.get("quote", True)):
            idx = set([])
            normalized_answer = normalize_arabic_digits(answer) or ""
            if embd_mdl and not CITATION_MARKER_PATTERN.search(normalized_answer):
                # Main retrieval no longer ships chunk vectors back from ES.
                # Pull them on demand for the chunks we are about to cite.
                await _hydrate_chunk_vectors(retriever, kbinfos.get("chunks", []), tenant_ids, dialog.kb_ids)
                answer, idx = retriever.insert_citations(
                    answer,
                    [ck["content_ltks"] for ck in kbinfos["chunks"]],
                    [ck["vector"] for ck in kbinfos["chunks"]],
                    embd_mdl,
                    tkweight=1 - dialog.vector_similarity_weight,
                    vtweight=dialog.vector_similarity_weight,
                )
            else:
                for match in CITATION_MARKER_PATTERN.finditer(normalized_answer):
                    i = int(match.group(1))
                    if i < len(kbinfos["chunks"]):
                        idx.add(i)

            answer, idx = repair_bad_citation_formats(answer, kbinfos, idx)

            idx = set([kbinfos["chunks"][int(i)]["doc_id"] for i in idx])
            recall_docs = [d for d in kbinfos["doc_aggs"] if d["doc_id"] in idx]
            if not recall_docs:
                recall_docs = kbinfos["doc_aggs"]
            kbinfos["doc_aggs"] = recall_docs

            refs = deepcopy(kbinfos)
            for c in refs["chunks"]:
                if c.get("vector"):
                    del c["vector"]

        if answer.lower().find("invalid key") >= 0 or answer.lower().find("invalid api") >= 0:
            answer += " Please set LLM API-Key in 'User Setting -> Model providers -> API-Key'"
        finish_chat_ts = timer()

        total_time_cost = (finish_chat_ts - chat_start_ts) * 1000
        check_llm_time_cost = (check_llm_ts - chat_start_ts) * 1000
        check_langfuse_tracer_cost = (check_langfuse_tracer_ts - check_llm_ts) * 1000
        bind_embedding_time_cost = (bind_models_ts - check_langfuse_tracer_ts) * 1000
        refine_question_time_cost = (refine_question_ts - bind_models_ts) * 1000
        retrieval_time_cost = (retrieval_ts - refine_question_ts) * 1000
        generate_result_time_cost = (finish_chat_ts - retrieval_ts) * 1000

        tk_num = num_tokens_from_string(think + answer)
        prompt += "\n\n### Query:\n%s" % " ".join(questions)
        prompt = (
            f"{prompt}\n\n"
            "## Time elapsed:\n"
            f"  - Total: {total_time_cost:.1f}ms\n"
            f"  - Check LLM: {check_llm_time_cost:.1f}ms\n"
            f"  - Check Langfuse tracer: {check_langfuse_tracer_cost:.1f}ms\n"
            f"  - Bind models: {bind_embedding_time_cost:.1f}ms\n"
            f"  - Query refinement(LLM): {refine_question_time_cost:.1f}ms\n"
            f"  - Retrieval: {retrieval_time_cost:.1f}ms\n"
            f"  - Generate answer: {generate_result_time_cost:.1f}ms\n\n"
            "## Token usage:\n"
            f"  - Generated tokens(approximately): {tk_num}\n"
            f"  - Token speed: {int(tk_num / (generate_result_time_cost / 1000.0))}/s"
        )

        # Add a condition check to call the end method only if langfuse_generation exists
        if langfuse_generation is not None:
            langfuse_output = "\n" + re.sub(r"^.*?(### Query:.*)", r"\1", prompt, flags=re.DOTALL)
            langfuse_output = {"time_elapsed:": re.sub(r"\n", "  \n", langfuse_output), "created_at": time.time()}
            langfuse_generation.update(
                output=langfuse_output,
                usage_details={
                    "input": used_token_count,
                    "output": tk_num,
                    "total": used_token_count + tk_num,
                },
            )
            langfuse_generation.end()

        return {"answer": think + answer, "reference": refs, "prompt": re.sub(r"\n", "  \n", prompt), "created_at": time.time()}

    if langfuse_tracer:
        try:
            observation_kwargs = {
                "as_type": "generation",
                "trace_context": trace_context,
                "name": "chat",
                "model": llm_model_config["llm_name"],
                "input": {"prompt": prompt, "prompt4citation": prompt4citation, "messages": msg},
            }
            if session_id:
                with propagate_attributes(session_id=session_id):
                    langfuse_generation = langfuse_tracer.start_observation(**observation_kwargs)
            else:
                langfuse_generation = langfuse_tracer.start_observation(**observation_kwargs)
        except Exception as e:  # noqa: BLE001 - tracing must not break chat flow
            logger.warning("Langfuse start_observation failed; continuing without tracing: %s", e)
            langfuse_tracer = None
            langfuse_generation = None

    if stream:
        if llm_model_config["model_type"] == "chat":
            stream_iter = chat_mdl.async_chat_streamly_delta(prompt + prompt4citation, msg[1:], gen_conf)
        else:
            stream_iter = chat_mdl.async_chat_streamly_delta(prompt + prompt4citation, msg[1:], gen_conf, images=image_files)
        last_state = None
        async for kind, value, state in _stream_with_think_delta(stream_iter):
            last_state = state
            if kind == "marker":
                flags = {"start_to_think": True} if value == "<think>" else {"end_to_think": True}
                yield {"answer": "", "reference": {}, "final": False, **flags}
                continue
            yield {"answer": value, "reference": {}, "final": False}
        full_answer = last_state.full_text if last_state else ""
        if full_answer:
            final = await decorate_answer(_extract_visible_answer(thought + full_answer))
            final["final"] = True

            final["answer"] = ""
            yield final
    else:
        if llm_model_config["model_type"] == "chat":
            answer = await chat_mdl.async_chat(prompt + prompt4citation, msg[1:], gen_conf)
        else:
            answer = await chat_mdl.async_chat(prompt + prompt4citation, msg[1:], gen_conf, images=image_files)
        user_content = msg[-1].get("content", "[content not available]")
        logging.debug("User: {}|Assistant: {}".format(user_content, answer))
        res = await decorate_answer(answer)

        yield res

    return


class _ThinkStreamState:
    def __init__(self) -> None:
        self.full_text = ""
        self.last_idx = 0
        self.last_model_full = ""
        self.in_think = False
        self.close_pending = False
        self.pending_after_close = ""
        self.think_buffer = ""
        self.answer_buffer = ""


def _extract_visible_answer(text: str) -> str:
    text = text or ""
    if "</think>" not in text:
        return re.sub(r"</?think>", "", text)

    thought, answer = text.rsplit("</think>", 1)
    thought = re.sub(r"</?think>", "", thought).strip()
    answer = re.sub(r"</?think>", "", answer)
    if not thought:
        return answer
    return f"<think>{thought}</think>{answer}"


async def _stream_with_think_delta(stream_iter, min_tokens: int = 16):
    state = _ThinkStreamState()

    def _emit_text(section: str, text: str):
        if not text:
            return None
        if section == "think":
            return text
        state.answer_buffer += text
        if num_tokens_from_string(state.answer_buffer) >= min_tokens:
            out = state.answer_buffer
            state.answer_buffer = ""
            return out
        return None

    def _flush_think_buffer():
        if not state.think_buffer:
            return None
        out = state.think_buffer
        state.think_buffer = ""
        return out

    def _flush_answer_buffer():
        if not state.answer_buffer:
            return None
        out = state.answer_buffer
        state.answer_buffer = ""
        return out

    async for chunk in stream_iter:
        if not chunk:
            continue
        if chunk.startswith(state.last_model_full):
            new_part = chunk[len(state.last_model_full) :]
            state.last_model_full = chunk
        else:
            new_part = chunk
            state.last_model_full += chunk
        if not new_part:
            continue
        state.full_text += new_part
        pending = new_part

        if state.close_pending and "</think>" not in pending:
            state.close_pending = False
            think_piece = _flush_think_buffer()
            if think_piece is not None:
                yield ("text", think_piece, state)
            state.in_think = False
            yield ("marker", "</think>", state)
            if state.pending_after_close:
                answer_piece = state.pending_after_close
                state.pending_after_close = ""
                out = _emit_text("answer", answer_piece)
                if out is not None:
                    yield ("text", out, state)
            answer_piece = re.sub(r"</?think>", "", pending or "")
            if answer_piece:
                out = _emit_text("answer", answer_piece)
                if out is not None:
                    yield ("text", out, state)
            continue

        while pending:
            open_idx = pending.find("<think>")
            close_idx = pending.find("</think>")

            if open_idx == -1 and close_idx == -1:
                piece = re.sub(r"</?think>", "", pending or "")
                if piece:
                    section = "think" if state.in_think else "answer"
                    out = _emit_text(section, piece)
                    if out is not None:
                        yield ("text", out, state)
                break

            if open_idx != -1 and (close_idx == -1 or open_idx < close_idx):
                before = pending[:open_idx]
                if before:
                    piece = re.sub(r"</?think>", "", before or "")
                    section = "think" if state.in_think else "answer"
                    out = _emit_text(section, piece)
                    if out is not None:
                        yield ("text", out, state)
                pending = pending[open_idx + len("<think>") :]
                if not state.in_think:
                    answer_piece = _flush_answer_buffer()
                    if answer_piece is not None:
                        yield ("text", answer_piece, state)
                    think_piece = _flush_think_buffer()
                    if think_piece is not None:
                        yield ("text", think_piece, state)
                    state.in_think = True
                    yield ("marker", "<think>", state)
                continue

            before = pending[:close_idx]
            after = pending[close_idx + len("</think>") :]
            if before:
                piece = re.sub(r"</?think>", "", before or "")
                section = "think" if state.in_think else "answer"
                out = _emit_text(section, piece)
                if out is not None:
                    yield ("text", out, state)
            after_visible = re.sub(r"</?think>", "", after or "")
            if after_visible.strip():
                think_piece = _flush_think_buffer()
                if think_piece is not None:
                    yield ("text", think_piece, state)
                state.in_think = False
                yield ("marker", "</think>", state)
                pending = after_visible
                continue
            state.close_pending = True
            if after_visible:
                state.pending_after_close += after_visible
            pending = ""
            break

    if state.think_buffer:
        yield ("text", state.think_buffer, state)
        state.think_buffer = ""
    if state.close_pending:
        state.in_think = False
        yield ("marker", "</think>", state)
    if state.answer_buffer:
        yield ("text", state.answer_buffer, state)
        state.answer_buffer = ""
    if state.pending_after_close:
        yield ("text", state.pending_after_close, state)
        state.pending_after_close = ""


async def async_ask(question, kb_ids, tenant_id, chat_llm_name=None, search_config={}, search_id=None):
    doc_ids = search_config.get("doc_ids", [])
    rerank_mdl = None
    kb_ids = search_config.get("kb_ids", kb_ids)
    chat_llm_name = search_config.get("chat_id", chat_llm_name)
    rerank_id = search_config.get("rerank_id", "")
    meta_data_filter = search_config.get("meta_data_filter")
    include_reference_metadata, metadata_fields = _resolve_reference_metadata(search_config)

    kbs = KnowledgebaseService.get_by_ids(kb_ids)
    if not kbs:
        if not kb_ids:
            error = "**ERROR**: No KB selected"
        else:
            error = "**ERROR**: The selected KB is not valid"
        yield {"answer": error, "reference": {}, "final": True}
        return

    embedding_list = list(set([kb.embd_id for kb in kbs]))

    retriever = settings.retriever
    embd_owner_tenant_id = kbs[0].tenant_id
    embd_model_config = resolve_model_config(embd_owner_tenant_id, LLMType.EMBEDDING, embedding_list[0])
    embd_mdl = LLMBundle(embd_owner_tenant_id, embd_model_config)
    chat_model_config = resolve_model_config(tenant_id, LLMType.CHAT, chat_llm_name)
    chat_mdl = LLMBundle(tenant_id, chat_model_config)
    if rerank_id:
        rerank_model_config = resolve_model_config(tenant_id, LLMType.RERANK, rerank_id)
        rerank_mdl = LLMBundle(tenant_id, rerank_model_config)
    max_tokens = chat_mdl.max_length
    tenant_ids = list(set([kb.tenant_id for kb in kbs]))

    if meta_data_filter:
        doc_ids = await apply_meta_data_filter(
            meta_data_filter,
            None,
            question,
            chat_mdl,
            doc_ids,
            kb_ids=kb_ids,
            metas_loader=lambda: DocMetadataService.get_flatted_meta_by_kbs(kb_ids),
        )

    vector_similarity_weight = search_config.get("vector_similarity_weight", 0.3)
    try:
        full_text_weight = 1 - vector_similarity_weight
    except TypeError:
        full_text_weight = None
    logger.debug(
        "Search async_ask retrieval weight: search_id=%s tenant_id=%s kb_count=%s vector_similarity_weight=%s full_text_weight=%s",
        search_id,
        tenant_id,
        len(kb_ids),
        vector_similarity_weight,
        full_text_weight,
    )

    kbinfos = await retriever.retrieval(
        question=question,
        embd_mdl=embd_mdl,
        tenant_ids=tenant_ids,
        kb_ids=kb_ids,
        page=1,
        page_size=12,
        similarity_threshold=search_config.get("similarity_threshold", 0.1),
        vector_similarity_weight=vector_similarity_weight,
        knn_top_k=search_config.get("top_k", 1024),
        doc_ids=doc_ids,
        aggs=True,
        rerank_mdl=rerank_mdl,
        rank_feature=label_question(question, kbs),
        trace_id=search_id,
        rerank_candidates_count=search_config.get("rerank_candidates_count", 100),
    )
    if include_reference_metadata:
        logging.debug(
            "reference_metadata enrichment enabled for async_ask: chunk_count=%d metadata_fields=%s",
            len(kbinfos.get("chunks", [])),
            metadata_fields,
        )
        _enrich_chunks_with_document_metadata(kbinfos.get("chunks", []), metadata_fields)

    knowledges = kb_prompt(kbinfos, max_tokens)
    sys_prompt = PROMPT_JINJA_ENV.from_string(ASK_SUMMARY).render(knowledge="\n".join(knowledges))

    msg = [{"role": "user", "content": question}]

    async def decorate_answer(answer):
        nonlocal knowledges, kbinfos, sys_prompt
        # Main retrieval no longer ships chunk vectors back from ES. Pull
        # them on demand for the chunks we are about to cite.
        await _hydrate_chunk_vectors(retriever, kbinfos.get("chunks", []), tenant_ids, kb_ids)
        answer, idx = retriever.insert_citations(answer, [ck["content_ltks"] for ck in kbinfos["chunks"]], [ck["vector"] for ck in kbinfos["chunks"]], embd_mdl, tkweight=0.7, vtweight=0.3)
        idx = set([kbinfos["chunks"][int(i)]["doc_id"] for i in idx])
        recall_docs = [d for d in kbinfos["doc_aggs"] if d["doc_id"] in idx]
        if not recall_docs:
            recall_docs = kbinfos["doc_aggs"]
        kbinfos["doc_aggs"] = recall_docs
        refs = deepcopy(kbinfos)
        for c in refs["chunks"]:
            if c.get("vector"):
                del c["vector"]

        if answer.lower().find("invalid key") >= 0 or answer.lower().find("invalid api") >= 0:
            answer += " Please set LLM API-Key in 'User Setting -> Model Providers -> API-Key'"
        refs["chunks"] = chunks_format(refs)
        return {"answer": answer, "reference": refs}

    gen_conf = resolve_llm_setting(search_config.get("llm_setting"))
    if "parameter" in gen_conf:
        del gen_conf["parameter"]
    stream_iter = chat_mdl.async_chat_streamly_delta(sys_prompt, msg, gen_conf)
    last_state = None
    async for kind, value, state in _stream_with_think_delta(stream_iter):
        last_state = state
        if kind == "marker":
            flags = {"start_to_think": True} if value == "<think>" else {"end_to_think": True}
            yield {"answer": "", "reference": {}, "final": False, **flags}
            continue
        yield {"answer": value, "reference": {}, "final": False}
    full_answer = last_state.full_text if last_state else ""
    final = await decorate_answer(_extract_visible_answer(full_answer))
    final["final"] = True
    final["answer"] = ""
    yield final
