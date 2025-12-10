# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import pytest
from fastapi.testclient import TestClient
from src.server import app
import shutil
from src.handlers import http
from src.sandbox.manager import SandboxManager

# This client is used by the non-gVisor tests
client = TestClient(app)

runsc_path = shutil.which("runsc")

def test_status_endpoint():
    """
    Tests that the /status endpoint returns a 200 OK response.
    """
    response = client.get("/status")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
@pytest.mark.parametrize("language, code, expected_stdout, expected_stderr", [
    ("python", "import sys; print('hello'); print('world', file=sys.stderr)", "hello", "world"),
    ("bash", "echo 'hello from bash'; echo 'error from bash' >&2", "hello from bash", "error from bash"),
])
def test_execute_streaming_gvisor(language, code, expected_stdout, expected_stderr):
    """
    Tests the successful execution of code via the streaming HTTP endpoint using a gVisor sandbox.
    This is an integration test that requires 'runsc' to be in the PATH.
    """
    with TestClient(app) as client:
        response = client.post(f"/execute?language={language}", content=code, headers={"Content-Type": "text/plain"})
        assert response.status_code == 200
        assert expected_stdout in response.text
        assert expected_stderr in response.text
