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

from quart import request, make_response
from api.apps import AUTH_JWT, AUTH_API, AUTH_BETA, login_required
from api.utils.api_utils import add_tenant_id_to_kwargs, get_data_error_result, server_error_response
from api.utils.file_response import apply_download_file_response_headers, apply_preview_file_response_headers, resolve_attachment_content_type
from common import settings
from common.misc_utils import thread_pool_exec


def _attachment_request_metadata():
    ext = request.args.get("ext")
    mime_type = request.args.get("mime_type")
    filename = request.args.get("filename")
    content_type, resolved_ext = resolve_attachment_content_type(ext, mime_type)
    if not content_type:
        content_type = "application/octet-stream"
    if not resolved_ext:
        resolved_ext = ext.lower().strip(".") if isinstance(ext, str) and ext else "bin"
    return content_type, resolved_ext, filename


async def _stream_attachment(tenant_id, attachment_id, *, inline: bool):
    attachment_id = attachment_id or request.view_args.get("attachment_id")
    content_type, ext, filename = _attachment_request_metadata()
    data = await thread_pool_exec(settings.STORAGE_IMPL.get, tenant_id, attachment_id)
    if not data:
        return get_data_error_result(message="document not found")
    response = await make_response(data)
    if inline:
        apply_preview_file_response_headers(response, content_type, ext, filename)
    else:
        apply_download_file_response_headers(response, content_type, ext, filename)
    return response


@manager.route("/agents/attachments/<attachment_id>/preview", methods=["GET"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def preview_attachment(tenant_id=None, attachment_id=None):
    """Stream an ordinary attachment for inline preview."""
    try:
        return await _stream_attachment(tenant_id, attachment_id, inline=True)
    except Exception as e:
        return server_error_response(e)


@manager.route("/agents/attachments/<attachment_id>/download", methods=["GET"])  # noqa: F821
@login_required(auth_types=[AUTH_JWT, AUTH_API, AUTH_BETA])
@add_tenant_id_to_kwargs
async def download_attachment(tenant_id=None, attachment_id=None):
    """Stream an ordinary attachment as a download."""
    try:
        if request.args.get("disposition", "").lower() == "inline":
            return await _stream_attachment(tenant_id, attachment_id, inline=True)
        return await _stream_attachment(tenant_id, attachment_id, inline=False)
    except Exception as e:
        return server_error_response(e)
