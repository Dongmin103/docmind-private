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
import logging
import re

from quart import make_response
from api.apps import login_required
from api.db import FileType
from api.db.services.file2document_service import File2DocumentService
from api.utils.api_utils import (
    add_tenant_id_to_kwargs,
    get_error_data_result,
)
from api.utils.web_utils import CONTENT_TYPE_MAP, apply_safe_file_response_headers
from common import settings
from common.misc_utils import thread_pool_exec
from api.apps.services import file_read_service


@manager.route("/files/<file_id>", methods=["GET"])  # noqa: F821
@login_required
@add_tenant_id_to_kwargs
async def download(tenant_id: str = None, file_id: str = None):
    """
    Download a file.
    ---
    tags:
      - Files
    security:
      - ApiKeyAuth: []
    produces:
      - application/octet-stream
    parameters:
      - in: path
        name: file_id
        type: string
        required: true
        description: File ID to download.
    responses:
      200:
        description: File stream.
    """
    try:
        success, result = file_read_service.get_file_content(tenant_id, file_id)
        if not success:
            return get_error_data_result(message=result)

        file = result
        blob = await thread_pool_exec(settings.STORAGE_IMPL.get, file.parent_id, file.location)
        if not blob:
            b, n = File2DocumentService.get_storage_address(file_id=file_id)
            blob = await thread_pool_exec(settings.STORAGE_IMPL.get, b, n)
        if not blob:
            logging.warning(
                "Download failed: empty blob after primary+fallback lookup (tenant_id=%s, file_id=%s)",
                tenant_id,
                file_id,
            )
            return get_error_data_result(message="This file is empty.")

        response = await make_response(blob)
        ext = re.search(r"\.([^.]+)$", file.name.lower())
        ext = ext.group(1) if ext else None
        content_type = None
        if ext:
            fallback_prefix = "image" if file.type == FileType.VISUAL.value else "application"
            content_type = CONTENT_TYPE_MAP.get(ext, f"{fallback_prefix}/{ext}")
        apply_safe_file_response_headers(response, content_type, ext)
        return response
    except Exception as e:
        logging.exception(e)
        return get_error_data_result(message="Internal server error")
