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
from unittest.mock import patch, AsyncMock
from src.sandbox.fake import FakeSandbox, FakeSandboxConfig, ExecConfig
from src.sandbox.types import SandboxOutputEvent, OutputType, CodeLanguage, SandboxStateEvent
from src.sandbox.interface import SandboxStreamClosed
from src.handlers import http
from src.sandbox.manager import SandboxManager
from fastapi import BackgroundTasks

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_manager():
    """
    Fixture to set up and tear down the manager for each test.
    This ensures that each test runs in isolation.
    """
    # Create a new manager for each test
    manager = SandboxManager()
    # Inject the manager into the http module
    http.manager = manager
    yield manager
    # Teardown is handled by the test client's lifespan management

@patch('src.handlers.http.list_containers')
def test_list_containers_success(mock_list_containers):
    """Tests the successful listing of containers."""
    mock_list_containers.return_value = ("container1\ncontainer2", None)
    
    response = client.get("/list")
    assert response.status_code == 200
    assert response.json() == {"containers": "container1\ncontainer2"}

@patch('src.sandbox.manager.SandboxManager.create_sandbox')
@patch('src.sandbox.manager.SandboxManager.delete_sandbox')
def test_execute_code_streaming(mock_delete_sandbox, mock_create_sandbox):
    """Tests the streaming execution of code."""
    # Arrange
    output_stream = [
        {"type": "status_update", "status": "SANDBOX_EXECUTION_RUNNING"},
        SandboxOutputEvent(type=OutputType.STDOUT, data="line 1\n"),
        SandboxOutputEvent(type=OutputType.STDOUT, data="line 2\n"),
        {"type": "status_update", "status": "SANDBOX_EXECUTION_DONE"},
    ]
    config = FakeSandboxConfig(executions=[ExecConfig(output_stream=output_stream)])
    sandbox = FakeSandbox("fake-sandbox-http", config=config)
    sandbox.execute = AsyncMock() # Mock the execute method
    
    mock_create_sandbox.return_value = sandbox
    mock_delete_sandbox.return_value = None

    # Act
    response = client.post(
        "/execute?language=python",
        content="print('hello')",
        headers={"Content-Type": "text/plain"}
    )

    # Assert
    assert response.status_code == 200
    assert response.text == "line 1\nline 2\n"
    mock_create_sandbox.assert_called_once()
    sandbox.execute.assert_called_once_with(CodeLanguage.PYTHON, "print('hello')")
    mock_delete_sandbox.assert_called_once_with("fake-sandbox-http")

@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.create_sandbox')
@patch('src.sandbox.manager.SandboxManager.delete_sandbox')
async def test_execute_code_streaming_handles_stream_closed(mock_delete_sandbox, mock_create_sandbox):
    """
    Tests that the execute_code_streaming generator handles SandboxStreamClosed gracefully.
    """
    # Arrange
    sandbox = FakeSandbox("fake-sandbox-stream-closed")
    sandbox.execute = AsyncMock()
    
    async def mock_stream_outputs():
        yield SandboxOutputEvent(type=OutputType.STDOUT, data="line 1\n")
        raise SandboxStreamClosed()

    sandbox.stream_outputs = mock_stream_outputs
    
    mock_create_sandbox.return_value = sandbox
    mock_delete_sandbox.return_value = None
    
    background_tasks = BackgroundTasks()

    # Act
    stream = [item async for item in http.execute_code_streaming(CodeLanguage.PYTHON, "test", background_tasks)]
    
    # Manually run the background tasks
    for task in background_tasks.tasks:
        await task()

    # Assert
    assert len(stream) == 1
    assert stream[0] == b"line 1\n"
    mock_delete_sandbox.assert_called_once_with("fake-sandbox-stream-closed")
