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
import subprocess
import time
import os
import tempfile
from pathlib import Path
import requests
import signal
from unittest.mock import patch, AsyncMock
from src.server import lifespan, app
import asyncio
import sys

def test_server_sigterm_shutdown_deletes_sandboxes():
    """
    Tests that sending a SIGTERM to the server correctly triggers the
    lifespan shutdown hook and calls the sandbox manager to delete all
    running sandboxes. This is a full end-to-end integration test.
    """
    # 1. Create a temporary file to act as a signal.
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        signal_file_path = tmp.name
    
    # 2. Ensure the file does not exist before the test.
    if os.path.exists(signal_file_path):
        os.remove(signal_file_path)

    # 3. Set the environment variable for the subprocess.
    env = os.environ.copy()
    env["SHUTDOWN_TEST_FILE"] = signal_file_path
    env["PORT"] = "8001"

    server_process = None
    try:
        # 4. Start the server using the correct entrypoint and interpreter.
        server_process = subprocess.Popen(
            [sys.executable, "main.py"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        # 5. Poll the server to wait for it to start up.
        start_time = time.time()
        while time.time() - start_time < 20: # 20-second timeout for CI
            try:
                response = requests.get("http://127.0.0.1:8001/")
                if response.status_code == 200:
                    break
            except requests.ConnectionError:
                time.sleep(0.1) # Wait a bit before retrying
        else:
            # If the server fails to start, provide diagnostic output.
            stdout, stderr = server_process.communicate()
            pytest.fail(
                "Server did not start within 20 seconds.\n"
                f"STDOUT: {stdout.decode()}\n"
                f"STDERR: {stderr.decode()}"
            )

        # 6. Send the SIGTERM signal directly to the server process.
        os.kill(server_process.pid, signal.SIGTERM)

        # 7. Wait for the process to exit gracefully.
        stdout, stderr = server_process.communicate(timeout=10)

        # 8. Assert that the signal file was created.
        assert Path(signal_file_path).exists(), f"Shutdown signal file was not created. Server stderr: {stderr.decode()}"

    finally:
        # 9. Clean up the signal file and ensure the process is terminated.
        if server_process and server_process.poll() is None:
            server_process.kill()
        if os.path.exists(signal_file_path):
            os.remove(signal_file_path)

@pytest.mark.asyncio
@patch('src.server.SandboxManager', autospec=True)
async def test_lifespan_shutdown_hook(MockSandboxManager):
    """
    Directly tests that the lifespan shutdown hook correctly calls the sandbox
    manager. This verifies the application logic without a full server process.
    """
    mock_manager_instance = MockSandboxManager.return_value
    mock_manager_instance.delete_all_sandboxes = AsyncMock()

    async with lifespan(app):
        # Simulate the app running
        mock_manager_instance.delete_all_sandboxes.assert_not_awaited()

    # When the context exits, the shutdown hook should be called.
    mock_manager_instance.delete_all_sandboxes.assert_awaited_once()
