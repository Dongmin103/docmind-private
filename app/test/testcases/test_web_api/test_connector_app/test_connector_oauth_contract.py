#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
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

import pytest
import requests

from configs import HOST_ADDRESS, VERSION

LLM_API_KEY_URL = f"{HOST_ADDRESS}/{VERSION}/llm/set_api_key"
LANGFUSE_API_KEY_URL = f"{HOST_ADDRESS}/api/{VERSION}/langfuse/api-key"

pytestmark = pytest.mark.p3


@pytest.fixture(autouse=True)
def _require_oauth_env(require_env_flag):
    require_env_flag("RAGFLOW_E2E_OAUTH")


def _assert_unauthorized(payload):
    assert payload["code"] == 401, payload
    assert "Unauthorized" in payload["message"], payload


def _assert_unauthorized_response(res, *, allow_405=False):
    if allow_405 and res.status_code == 405:
        pytest.skip("method not supported in this deployment")
    content_type = res.headers.get("Content-Type", "")
    payload = None
    if "json" in content_type:
        payload = res.json()
    else:
        try:
            payload = res.json()
        except ValueError:
            assert False, f"Expected JSON response, status={res.status_code}, content_type={content_type}"
    _assert_unauthorized(payload)


def test_langfuse_api_key_requires_auth():
    res = requests.post(LANGFUSE_API_KEY_URL, json={})
    _assert_unauthorized_response(res)


def test_langfuse_api_key_requires_auth_get():
    res = requests.get(LANGFUSE_API_KEY_URL)
    _assert_unauthorized_response(res, allow_405=True)


def test_langfuse_api_key_requires_auth_put():
    res = requests.put(LANGFUSE_API_KEY_URL, json={})
    _assert_unauthorized_response(res, allow_405=True)


def test_llm_set_api_key_requires_auth():
    res = requests.post(LLM_API_KEY_URL, json={})
    _assert_unauthorized_response(res)
