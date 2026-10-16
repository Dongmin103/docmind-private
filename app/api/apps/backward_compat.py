#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
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
"""
Backward compatibility layer for deprecated API endpoints.

This module adds support for old API routes that were deprecated during the
RESTful API migration. Each deprecated route forwards to the corresponding
new API implementation.

Deprecated APIs and their replacements:
- POST /api/v1/chats/{chat_id}/completions -> POST /api/v1/chat/completions
- POST /api/v1/chats_openai/{chat_id}/chat/completions -> POST /api/v1/openai/{chat_id}/chat/completions
- PUT /api/v1/chats/{chat_id}/sessions/{session_id} -> PATCH /api/v1/chats/{chat_id}/sessions/{session_id}
- DELETE /api/v1/chats -> DELETE /api/v1/chats/{chat_id} (with body)
- POST /v1/document/upload_info -> POST /api/v1/documents/upload
- GET /api/v1/file/* -> GET /api/v1/files*
- POST /api/v1/file/* -> POST /api/v1/files*
- GET /api/v1/document/get/{doc_id} -> GET /api/v1/documents/{doc_id}/preview
- GET /v1/system/healthz -> GET /api/v1/system/healthz
- POST /api/v1/sessions/related_questions -> POST /api/v1/chat/recommendation
- PUT (chunk update) -> PATCH (chunk update)
"""

import logging

from quart import Blueprint, jsonify

from api.apps import login_required
from api.apps.restful_apis import chat_api, chunk_api, document_api, file_api, openai_api
from api.apps.restful_apis.system_api import run_health_checks

manager = Blueprint("backward_compat", __name__)
legacy_v1_manager = Blueprint("backward_compat_v1", __name__)


# =============================================================================
# System APIs
# =============================================================================


@legacy_v1_manager.route("/system/healthz", methods=["GET"])
async def deprecated_system_healthz():
    """
    Deprecated: Use GET /api/v1/system/healthz instead.

    Old path: GET /v1/system/healthz
    New path: GET /api/v1/system/healthz
    """
    logging.warning("API endpoint /v1/system/healthz is deprecated. Please use /api/v1/system/healthz instead.")
    result, all_ok = run_health_checks()
    return jsonify(result), (200 if all_ok else 500)


# =============================================================================
# Chat Completion APIs
# =============================================================================


@manager.route("/chats/<chat_id>/completions", methods=["POST"])
@login_required
async def deprecated_chat_completions(chat_id):
    """
    Deprecated: Use POST /api/v1/chat/completions instead.

    Old path: POST /api/v1/chats/{chat_id}/completions
    New path: POST /api/v1/chat/completions
    """
    logging.warning(
        "API endpoint /api/v1/chats/%s/completions is deprecated. Please use /api/v1/chat/completions instead.",
        chat_id,
    )
    # Forward to the new API implementation
    return await chat_api.session_completion(chat_id)


@manager.route("/chats_openai/<chat_id>/chat/completions", methods=["POST"])
@login_required
async def deprecated_openai_chat_completions(chat_id):
    """
    Deprecated: Use POST /api/v1/openai/{chat_id}/chat/completions instead.

    Old path: POST /api/v1/chats_openai/{chat_id}/chat/completions
    New path: POST /api/v1/openai/{chat_id}/chat/completions
    """
    logging.warning(
        "API endpoint /api/v1/chats_openai/%s/chat/completions is deprecated. Please use /api/v1/openai/%s/chat/completions instead.",
        chat_id,
        chat_id,
    )
    # Forward to the new API implementation
    return await openai_api.openai_chat_completions(chat_id)


# =============================================================================
# Dataset Graph and Index APIs
# =============================================================================


# =============================================================================
# Chat Session APIs
# =============================================================================


@manager.route("/chats/<chat_id>/sessions/<session_id>", methods=["PUT"])
@login_required
async def deprecated_update_session(chat_id, session_id):
    """
    Deprecated: Use PATCH /api/v1/chats/{chat_id}/sessions/{session_id} instead.

    Old path: PUT /api/v1/chats/{chat_id}/sessions/{session_id}
    New path: PATCH /api/v1/chats/{chat_id}/sessions/{session_id}
    """
    logging.warning(
        "API endpoint PUT /api/v1/chats/%s/sessions/%s is deprecated. Please use PATCH /api/v1/chats/%s/sessions/%s instead.",
        chat_id,
        session_id,
        chat_id,
        session_id,
    )
    # Forward to the new API implementation
    return await chat_api.update_session(chat_id, session_id)


# =============================================================================
# File APIs (Old /api/v1/file/* -> New /api/v1/files*)
# =============================================================================


@manager.route("/file/get/<file_id>", methods=["GET"])
@login_required
async def deprecated_file_get(file_id):
    """
    Deprecated: Use GET /api/v1/files/{file_id} instead.

    Old path: GET /api/v1/file/get/{file_id}
    New path: GET /api/v1/files/{file_id}
    """
    logging.warning(
        "API endpoint /api/v1/file/get/%s is deprecated. Please use /api/v1/files/%s instead.",
        file_id,
        file_id,
    )
    # Forward to the new API implementation (download)
    return await file_api.download(file_id=file_id)


# =============================================================================
# Related Questions API
# =============================================================================


@manager.route("/sessions/related_questions", methods=["POST"])
@login_required
async def deprecated_related_questions():
    """
    Deprecated: Use POST /api/v1/chat/recommendation instead.

    Old path: POST /api/v1/sessions/related_questions
    New path: POST /api/v1/chat/recommendation
    """
    logging.warning("API endpoint /api/v1/sessions/related_questions is deprecated. Please use /api/v1/chat/recommendation instead.")
    # Forward to the new API implementation
    return await chat_api.recommendation()


# =============================================================================
# Chunk Update API (PUT -> PATCH)
# =============================================================================


@manager.route("/datasets/<dataset_id>/documents/<document_id>/chunks/<chunk_id>", methods=["PUT"])
@login_required
async def deprecated_update_chunk(dataset_id, document_id, chunk_id):
    """
    Deprecated: Use PATCH /api/v1/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id} instead.

    Old path: PUT /api/v1/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}
    New path: PATCH /api/v1/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}
    """
    logging.warning(
        "API endpoint PUT /api/v1/datasets/%s/documents/%s/chunks/%s is deprecated. Please use PATCH instead.",
        dataset_id,
        document_id,
        chunk_id,
    )
    # Forward to the new API implementation
    return await chunk_api.update_chunk(dataset_id=dataset_id, document_id=document_id, chunk_id=chunk_id)


# =============================================================================
# File Upload Info API
# =============================================================================


@manager.route("/file/upload_info", methods=["POST"])
@login_required
async def deprecated_file_upload_info():
    """
    Deprecated: Use POST /api/v1/documents/upload instead.

    Old path: POST /api/v1/file/upload_info
    New path: POST /api/v1/documents/upload
    """
    from api.apps import current_user

    logging.warning("API endpoint /api/v1/file/upload_info is deprecated. Please use POST /api/v1/documents/upload instead.")
    # Forward to the new API implementation
    # Need to pass tenant_id explicitly since we're calling the function directly
    tenant_id = current_user.id
    return await document_api.upload_info(tenant_id=tenant_id)


@legacy_v1_manager.route("/document/upload_info", methods=["POST"])
@login_required
async def deprecated_legacy_document_upload_info():
    """
    Deprecated: Use POST /api/v1/documents/upload instead.

    Old path: POST /v1/document/upload_info
    New path: POST /api/v1/documents/upload
    """
    from api.apps import current_user

    logging.warning("API endpoint /v1/document/upload_info is deprecated. Please use POST /api/v1/documents/upload instead.")
    tenant_id = current_user.id
    return await document_api.upload_info(tenant_id=tenant_id)


# =============================================================================
# Document APIs
# =============================================================================


@manager.route("/datasets/<dataset_id>/documents/<document_id>", methods=["PUT"])
@login_required
async def deprecated_update_document(dataset_id, document_id):
    """
    Deprecated: Use PATCH /api/v1/datasets/{dataset_id}/documents/{document_id} instead.

    Old path: PUT /api/v1/datasets/{dataset_id}/documents/{document_id}
    New path: PATCH /api/v1/datasets/{dataset_id}/documents/{document_id}
    """
    logging.warning(
        "API endpoint PUT /api/v1/datasets/%s/documents/%s is deprecated. Please use PATCH instead.",
        dataset_id,
        document_id,
    )
    # Forward to the new API implementation
    return await document_api.update_document(dataset_id=dataset_id, document_id=document_id)


@manager.route("/document/get/<doc_id>", methods=["GET"])
@login_required
async def deprecated_document_get(doc_id):
    """
    Deprecated: Use GET /api/v1/documents/{doc_id}/preview instead.

    Old path: GET /api/v1/document/get/{doc_id}
    New path: GET /api/v1/documents/{doc_id}/preview
    """
    logging.warning(
        "API endpoint /api/v1/document/get/%s is deprecated. Please use /api/v1/documents/%s/preview instead.",
        doc_id,
        doc_id,
    )
    return await document_api.get(doc_id)


# =============================================================================
# =============================================================================


def register_backward_compat_routes(app_instance):
    """
    Register all backward compatibility routes with the app.
    """
    app_instance.register_blueprint(manager, url_prefix="/api/v1")
    app_instance.register_blueprint(legacy_v1_manager, url_prefix="/v1")
    logging.info("Backward compatibility routes registered successfully.")
