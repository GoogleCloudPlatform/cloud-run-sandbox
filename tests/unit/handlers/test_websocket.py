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
from src.sandbox.fake import FakeSandbox, FakeSandboxConfig, ExecConfig
from src.sandbox.interface import SandboxCreationError, SandboxExecutionError, SandboxOperationError, SandboxCheckpointError, SandboxRestoreError
from src.sandbox.types import SandboxOutputEvent, OutputType, CodeLanguage, SandboxStateEvent
from src.sandbox.config import GCSConfig
from src.handlers import websocket
from src.sandbox.manager import SandboxManager
from unittest.mock import patch, ANY, AsyncMock, MagicMock
from starlette.websockets import WebSocketDisconnect
import asyncio

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_manager(tmp_path):
    """
    Fixture to set up and tear down the manager for each test.
    This ensures that each test runs in isolation.
    """
    gcs_config = GCSConfig(
        metadata_mount_path=str(tmp_path),
        metadata_bucket="test-bucket",
        sandbox_checkpoint_mount_path=str(tmp_path),
        sandbox_checkpoint_bucket="test-bucket",
        filesystem_snapshot_mount_path=str(tmp_path),
        filesystem_snapshot_bucket="test-bucket",
    )
    manager = SandboxManager(gcs_config=gcs_config)
    websocket.manager = manager
    yield manager

@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.create_sandbox')
async def test_create_interactive_session_success(mock_create_sandbox):
    """
    Tests the successful creation of an interactive sandbox session,
    executing multiple commands.
    """
    # Arrange
    config = FakeSandboxConfig(executions=[
        ExecConfig(
            expected_language=CodeLanguage.PYTHON,
            expected_code="print('Hello Python')",
            output_stream=[SandboxOutputEvent(type=OutputType.STDOUT, data="Hello Python\n")]
        ),
        ExecConfig(
            expected_language=CodeLanguage.BASH,
            expected_code="echo 'Hello Bash'",
            output_stream=[SandboxOutputEvent(type=OutputType.STDOUT, data="Hello Bash\n")]
        )
    ])
    sandbox = FakeSandbox("interactive-sandbox", config=config)
    await sandbox.create()
    await sandbox.set_sandbox_token("test-token")
    mock_create_sandbox.return_value = sandbox

    # Act & Assert
    with client.websocket_connect("/create") as websocket:
        # 1. Send initial config and receive confirmation
        websocket.send_json({"idle_timeout": 120})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATING"}
        assert websocket.receive_json() == {
            "event": "sandbox_id",
            "sandbox_id": "interactive-sandbox",
            "sandbox_token": "test-token",
        }
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}

        # 2. Execute first command (Python)
        websocket.send_json({"language": "python", "code": "print('Hello Python')"})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_EXECUTION_RUNNING"}
        assert websocket.receive_json() == {"event": "stdout", "data": "Hello Python\n"}
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_EXECUTION_DONE"}

        # 3. Execute second command (Bash)
        websocket.send_json({"language": "bash", "code": "echo 'Hello Bash'"})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_EXECUTION_RUNNING"}
        assert websocket.receive_json() == {"event": "stdout", "data": "Hello Bash\n"}
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_EXECUTION_DONE"}

    # Assert that the sandbox was created with the correct idle timeout
    mock_create_sandbox.assert_called_once_with(idle_timeout=120, enable_checkpoint=False, enable_idle_timeout_auto_checkpoint=False, enable_sandbox_handoff=False, filesystem_snapshot_name=None, status_notifier=ANY)

@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.create_sandbox')
async def test_create_sandbox_with_filesystem_snapshot(mock_create_sandbox):
    """
    Tests that a sandbox can be created with a filesystem snapshot.
    """
    # Arrange
    sandbox = FakeSandbox("snapshot-sandbox")
    await sandbox.create()
    await sandbox.set_sandbox_token("test-token")
    mock_create_sandbox.return_value = sandbox

    # Act & Assert
    with client.websocket_connect("/create") as websocket:
        websocket.send_json({"filesystem_snapshot_name": "my-snapshot"})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATING"}
        assert websocket.receive_json() == {
            "event": "sandbox_id",
            "sandbox_id": "snapshot-sandbox",
            "sandbox_token": "test-token",
        }
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}

    # Assert that create_sandbox was called with the snapshot name
    mock_create_sandbox.assert_called_once_with(
        idle_timeout=300,
        enable_checkpoint=False,
        enable_idle_timeout_auto_checkpoint=False,
        enable_sandbox_handoff=False,
        filesystem_snapshot_name="my-snapshot",
        status_notifier=ANY
    )


@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.create_sandbox')
async def test_create_sandbox_with_handoff(mock_create_sandbox):
    """
    Tests that a sandbox can be created with handoff enabled.
    """
    # Arrange
    sandbox = FakeSandbox("handoff-sandbox")
    await sandbox.create()
    await sandbox.set_sandbox_token("test-token")
    mock_create_sandbox.return_value = sandbox

    # Act & Assert
    with client.websocket_connect("/create") as websocket:
        websocket.send_json({"enable_sandbox_handoff": True})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATING"}
        assert websocket.receive_json() == {
            "event": "sandbox_id",
            "sandbox_id": "handoff-sandbox",
            "sandbox_token": "test-token",
        }
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}

    # Assert that create_sandbox was called with handoff enabled
    mock_create_sandbox.assert_called_once_with(
        idle_timeout=300,
        enable_checkpoint=False,
        enable_idle_timeout_auto_checkpoint=False,
        enable_sandbox_handoff=True,
        filesystem_snapshot_name=None,
        status_notifier=ANY
    )


@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.create_sandbox')
async def test_interactive_session_with_stdin(mock_create_sandbox):
    """
    Tests that stdin is correctly handled during an interactive session.
    """
    # Arrange
    config = FakeSandboxConfig(executions=[
        ExecConfig(
            expected_language=CodeLanguage.PYTHON,
            expected_code="input()",
            expected_stdin=["Hello from test"],
            output_stream=[SandboxOutputEvent(type=OutputType.STDOUT, data="Hello from test\n")]
        )
    ])
    sandbox = FakeSandbox("stdin-sandbox", config=config)
    await sandbox.create()
    await sandbox.set_sandbox_token("test-token")
    mock_create_sandbox.return_value = sandbox

    # Act & Assert
    with client.websocket_connect("/create") as websocket:
        # 1. Send initial config and receive confirmation
        websocket.send_json({"idle_timeout": 120})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATING"}
        assert websocket.receive_json() == {
            "event": "sandbox_id",
            "sandbox_id": "stdin-sandbox",
            "sandbox_token": "test-token",
        }
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}

        # 2. Execute command
        websocket.send_json({"language": "python", "code": "input()"})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_EXECUTION_RUNNING"}

        # 3. Write to stdin
        websocket.send_json({"event": "stdin", "data": "Hello from test"})

        # 4. Receive output and completion
        assert websocket.receive_json() == {"event": "stdout", "data": "Hello from test\n"}
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_EXECUTION_DONE"}

@patch('src.sandbox.manager.SandboxManager.create_sandbox')
def test_create_sandbox_creation_error(mock_create_sandbox):
    """
    Tests that a sandbox creation error is handled gracefully.
    """
    # Arrange
    mock_create_sandbox.side_effect = SandboxCreationError("Failed to create sandbox")

    # Act & Assert
    with client.websocket_connect("/create") as websocket:
        websocket.send_json({"idle_timeout": 120})
        
        # Check for the status updates and the error message
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATING"}
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATION_ERROR"}
        assert websocket.receive_json() == {"event": "error", "message": "Failed to create sandbox"}
        
        # The connection should be closed after the error
        with pytest.raises(WebSocketDisconnect) as e:
            websocket.receive_json()
        assert e.value.code == 4000

@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.create_sandbox')
@patch('src.sandbox.manager.SandboxManager.get_sandbox')
async def test_create_and_attach_session(mock_get_sandbox, mock_create_sandbox):
    """
    Tests that a client can create a sandbox, disconnect, and another client
    can attach to it.
    """
    # Arrange
    sandbox = FakeSandbox("test-sandbox")
    await sandbox.set_sandbox_token("test-token")
    mock_create_sandbox.return_value = sandbox
    mock_get_sandbox.return_value = sandbox

    # Act & Assert
    # 1. Create the sandbox
    with client.websocket_connect("/create") as websocket:
        websocket.send_json({"idle_timeout": 120})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATING"}
        assert websocket.receive_json() == {
            "event": "sandbox_id",
            "sandbox_id": "test-sandbox",
            "sandbox_token": "test-token",
        }
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}

    # 2. Attach to the sandbox
    with client.websocket_connect(f"/attach/test-sandbox?sandbox_token=test-token") as websocket:
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}

@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.get_sandbox')
async def test_attach_to_in_use_sandbox(mock_get_sandbox):
    """
    Tests that attaching to a sandbox that is already in use fails.
    """
    # Arrange
    sandbox = FakeSandbox("test-sandbox")
    await sandbox.set_sandbox_token("test-token")
    sandbox.is_attached = True
    mock_get_sandbox.return_value = sandbox

    # Act & Assert
    with client.websocket_connect("/attach/test-sandbox?sandbox_token=test-token") as websocket:
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_IN_USE"}
        with pytest.raises(WebSocketDisconnect) as e:
            websocket.receive_json()
        assert e.value.code == 1011

@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.create_sandbox')
async def test_sandbox_execution_error(mock_create_sandbox):
    """
    Tests that a sandbox operation error is handled gracefully.
    """
    # Arrange
    config = FakeSandboxConfig(executions=[
        ExecConfig(
            expected_language=CodeLanguage.PYTHON,
            expected_code="print('will fail')",
            exec_error=SandboxExecutionError
        )
    ])
    sandbox = FakeSandbox("test-sandbox", config=config)
    await sandbox.create()
    await sandbox.set_sandbox_token("test-token")
    mock_create_sandbox.return_value = sandbox

    # Act & Assert
    with client.websocket_connect("/create") as websocket:
        websocket.send_json({"idle_timeout": 120})
        
        # Initial handshake
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATING"}
        assert websocket.receive_json() == {
            "event": "sandbox_id",
            "sandbox_id": "test-sandbox",
            "sandbox_token": "test-token",
        }
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}

        # Send code that will trigger the error
        websocket.send_json({"language": "python", "code": "print('will fail')"})

        # Check for the error message
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_EXECUTION_ERROR"}
        assert websocket.receive_json() == {"event": "error", "message": "Fake sandbox failed to execute as configured."}
        
        # The connection should remain open for subsequent commands
        # (This part of the test is not fully implemented as the FakeSandbox
        # is configured for a single execution)
        # websocket.send_json({"language": "python", "code": "print('still open')"})

@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.create_sandbox')
async def test_invalid_message_format(mock_create_sandbox):
    """
    Tests that the server handles an invalid message format gracefully.
    """
    # Arrange
    config = FakeSandboxConfig(executions=[
        ExecConfig(
            expected_language=CodeLanguage.PYTHON,
            expected_code="print('still open')",
            output_stream=[SandboxOutputEvent(type=OutputType.STDOUT, data="still open\n")]
        )
    ])
    sandbox = FakeSandbox("test-sandbox", config=config)
    await sandbox.create()
    await sandbox.set_sandbox_token("test-token")
    mock_create_sandbox.return_value = sandbox

    # Act & Assert
    with client.websocket_connect("/create") as websocket:
        websocket.send_json({"idle_timeout": 120})
        
        # Initial handshake
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATING"}
        assert websocket.receive_json() == {
            "event": "sandbox_id",
            "sandbox_id": "test-sandbox",
            "sandbox_token": "test-token",
        }
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}

        # Send a malformed message
        websocket.send_json({"invalid_key": "some_value"})

        # Check for the error message
        supported_languages = ", ".join([lang.value for lang in CodeLanguage])
        expected_error = (
            f"Unsupported language: 'None'. Supported languages are: {supported_languages}"
        )
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_EXECUTION_UNSUPPORTED_LANGUAGE_ERROR"}
        assert websocket.receive_json() == {"event": "error", "message": expected_error}

        # The connection should remain open
        websocket.send_json({"language": "python", "code": "print('still open')"})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_EXECUTION_RUNNING"}
        assert websocket.receive_json() == {"event": "stdout", "data": "still open\n"}
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_EXECUTION_DONE"}

@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.create_sandbox')
async def test_unsupported_language(mock_create_sandbox):
    """
    Tests that the server handles an unsupported language gracefully.
    """
    # Arrange
    config = FakeSandboxConfig(executions=[
        ExecConfig(
            expected_language=CodeLanguage.PYTHON,
            expected_code="print('still open')",
            output_stream=[SandboxOutputEvent(type=OutputType.STDOUT, data="still open\n")]
        )
    ])
    sandbox = FakeSandbox("test-sandbox", config=config)
    await sandbox.create()
    await sandbox.set_sandbox_token("test-token")
    mock_create_sandbox.return_value = sandbox

    # Act & Assert
    with client.websocket_connect("/create") as websocket:
        websocket.send_json({"idle_timeout": 120})
        
        # Initial handshake
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATING"}
        assert websocket.receive_json() == {
            "event": "sandbox_id",
            "sandbox_id": "test-sandbox",
            "sandbox_token": "test-token",
        }
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}

        # Send a message with an unsupported language
        websocket.send_json({"language": "unsupported", "code": "some code"})

        # Check for the error message
        supported_languages = ", ".join([lang.value for lang in CodeLanguage])
        expected_error = (
            f"Unsupported language: 'unsupported'. Supported languages are: {supported_languages}"
        )
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_EXECUTION_UNSUPPORTED_LANGUAGE_ERROR"}
        assert websocket.receive_json() == {"event": "error", "message": expected_error}

        # The connection should remain open
        websocket.send_json({"language": "python", "code": "print('still open')"})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_EXECUTION_RUNNING"}
        assert websocket.receive_json() == {"event": "stdout", "data": "still open\n"}
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_EXECUTION_DONE"}

@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.restore_sandbox')
@patch('src.sandbox.manager.SandboxManager.get_sandbox')
async def test_attach_restore_sandbox(mock_get_sandbox, mock_restore_sandbox):
    """
    Tests that a client can attach to a sandbox that has been checkpointed,
    triggering a restore.
    """
    # Arrange
    sandbox = FakeSandbox("test-sandbox")
    await sandbox.set_sandbox_token("test-token")
    mock_get_sandbox.return_value = None # Simulate cache miss
    mock_restore_sandbox.return_value = sandbox

    # Act & Assert
    with client.websocket_connect("/attach/test-sandbox?sandbox_token=test-token") as websocket:
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RESTORING"}
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}

def test_create_with_checkpoint_fails_if_not_configured(setup_manager):
    """
    Tests that creating a sandbox with checkpointing enabled fails if the
    manager is not configured with a persistence path.
    """
    setup_manager.gcs_config = None
    with client.websocket_connect("/create") as websocket:
        websocket.send_json({"enable_checkpoint": True})
        
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATING"}
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATION_ERROR"}
        assert websocket.receive_json() == {"event": "error", "message": "Checkpointing is not enabled on the server."}
        
        with pytest.raises(WebSocketDisconnect) as e:
            websocket.receive_json()
        assert e.value.code == 4000

@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.create_sandbox')
@patch('src.sandbox.manager.SandboxManager.checkpoint_sandbox')
async def test_sandbox_checkpoint(mock_checkpoint_sandbox, mock_create_sandbox):
    """
    Tests that the 'checkpoint' action is correctly handled.
    """
    # Arrange
    sandbox = FakeSandbox("test-sandbox")
    await sandbox.set_sandbox_token("test-token")
    mock_create_sandbox.return_value = sandbox

    # Act & Assert
    with client.websocket_connect("/create") as websocket:
        websocket.send_json({"idle_timeout": 120, "enable_checkpoint": True})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATING"}
        assert websocket.receive_json() == {
            "event": "sandbox_id",
            "sandbox_id": "test-sandbox",
            "sandbox_token": "test-token",
        }
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}
        websocket.send_json({"action": "checkpoint"})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CHECKPOINTING"}
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CHECKPOINTED"}
        with pytest.raises(WebSocketDisconnect) as e:
            websocket.receive_json()
        assert e.value.code == 1000

@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.create_sandbox')
@patch('src.sandbox.manager.SandboxManager.checkpoint_sandbox')
async def test_sandbox_checkpoint_failure(mock_checkpoint_sandbox, mock_create_sandbox):
    """
    Tests that a failure during checkpointing is handled gracefully.
    """
    # Arrange
    sandbox = FakeSandbox("test-sandbox")
    await sandbox.set_sandbox_token("test-token")
    mock_create_sandbox.return_value = sandbox
    mock_checkpoint_sandbox.side_effect = SandboxCheckpointError("Checkpoint failed")

    # Act & Assert
    with client.websocket_connect("/create") as websocket:
        websocket.send_json({"idle_timeout": 120, "enable_checkpoint": True})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATING"}
        assert websocket.receive_json() == {
            "event": "sandbox_id",
            "sandbox_id": "test-sandbox",
            "sandbox_token": "test-token",
        }
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}
        websocket.send_json({"action": "checkpoint"})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CHECKPOINTING"}
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CHECKPOINT_ERROR"}
        assert websocket.receive_json() == {"event": "error", "message": "Failed to checkpoint sandbox test-sandbox: Checkpoint failed"}
        with pytest.raises(WebSocketDisconnect) as e:
            websocket.receive_json()
        assert e.value.code == 4000

@patch('src.sandbox.manager.SandboxManager.restore_sandbox')
def test_attach_restore_sandbox_not_found(mock_restore_sandbox):
    """
    Tests that a failure to find a sandbox to restore is handled gracefully.
    """
    # Arrange
    mock_restore_sandbox.return_value = None

    # Act & Assert
    with client.websocket_connect("/attach/test-sandbox?sandbox_token=any-token") as websocket:
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RESTORING"}
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_NOT_FOUND"}
        with pytest.raises(WebSocketDisconnect) as e:
            websocket.receive_json()
        assert e.value.code == 1011

@patch('src.sandbox.manager.SandboxManager.restore_sandbox')
def test_attach_restore_sandbox_failure(mock_restore_sandbox):
    """
    Tests that a failure during restore is handled gracefully.
    """
    # Arrange
    mock_restore_sandbox.side_effect = SandboxRestoreError("Restore failed")

    # Act & Assert
    with client.websocket_connect("/attach/test-sandbox?sandbox_token=any-token") as websocket:
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RESTORING"}
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RESTORE_ERROR"}
        assert websocket.receive_json() == {"event": "error", "message": "Failed to restore sandbox test-sandbox: Restore failed"}
        with pytest.raises(WebSocketDisconnect) as e:
            websocket.receive_json()
        assert e.value.code == 4000

@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.create_sandbox')
@patch('src.sandbox.manager.SandboxManager.snapshot_filesystem')
async def test_snapshot_filesystem(mock_snapshot_filesystem, mock_create_sandbox):
    """
    Tests that the 'snapshot_filesystem' action is correctly handled.
    """
    # Arrange
    sandbox = FakeSandbox("test-sandbox")
    await sandbox.set_sandbox_token("test-token")
    mock_create_sandbox.return_value = sandbox

    # Act & Assert
    with client.websocket_connect("/create") as websocket:
        websocket.send_json({"idle_timeout": 120})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATING"}
        assert websocket.receive_json() == {
            "event": "sandbox_id",
            "sandbox_id": "test-sandbox",
            "sandbox_token": "test-token",
        }
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}

        websocket.send_json({"action": "snapshot_filesystem", "name": "my-snapshot"})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_FILESYSTEM_SNAPSHOT_CREATING"}
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_FILESYSTEM_SNAPSHOT_CREATED"}

    mock_snapshot_filesystem.assert_called_once_with("test-sandbox", "my-snapshot")

@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.create_sandbox')
async def test_snapshot_filesystem_not_configured(mock_create_sandbox, setup_manager):
    """
    Tests that taking a filesystem snapshot fails if the feature is not configured.
    """
    # Arrange
    sandbox = FakeSandbox("test-sandbox")
    await sandbox.set_sandbox_token("test-token")
    mock_create_sandbox.return_value = sandbox
    setup_manager.gcs_config = None

    # Act & Assert
    with client.websocket_connect("/create") as websocket:
        websocket.send_json({"idle_timeout": 120})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATING"}
        assert websocket.receive_json() == {
            "event": "sandbox_id",
            "sandbox_id": "test-sandbox",
            "sandbox_token": "test-token",
        }
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}

        websocket.send_json({"action": "snapshot_filesystem", "name": "my-snapshot"})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_FILESYSTEM_SNAPSHOT_CREATING"}
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_FILESYSTEM_SNAPSHOT_ERROR"}
        assert websocket.receive_json() == {"event": "error", "message": "Failed to snapshot filesystem for sandbox test-sandbox: Filesystem snapshot is not enabled on the server."}
@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.create_sandbox')
@patch('src.sandbox.manager.SandboxManager.snapshot_filesystem')
async def test_snapshot_filesystem_error(mock_snapshot_filesystem, mock_create_sandbox):
    """
    Tests that an error during filesystem snapshotting is handled gracefully.
    """
    # Arrange
    sandbox = FakeSandbox("test-sandbox")
    await sandbox.set_sandbox_token("test-token")
    mock_create_sandbox.return_value = sandbox
    mock_snapshot_filesystem.side_effect = SandboxOperationError("Snapshot failed")

    # Act & Assert
    with client.websocket_connect("/create") as websocket:
        websocket.send_json({"idle_timeout": 120})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATING"}
        assert websocket.receive_json() == {
            "event": "sandbox_id",
            "sandbox_id": "test-sandbox",
            "sandbox_token": "test-token",
        }
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}

        websocket.send_json({"action": "snapshot_filesystem", "name": "my-snapshot"})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_FILESYSTEM_SNAPSHOT_CREATING"}
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_FILESYSTEM_SNAPSHOT_ERROR"}
        assert websocket.receive_json() == {"event": "error", "message": "Failed to snapshot filesystem for sandbox test-sandbox: Snapshot failed"}

@pytest.mark.asyncio
async def test_websocket_status_notifier():
    """
    Tests that the WebSocketStatusNotifier correctly sends a status update.
    """
    # Arrange
    mock_websocket = MagicMock()
    mock_websocket.send_json = AsyncMock()
    
    notifier = websocket.WebSocketStatusNotifier(mock_websocket)
    
    # Act
    await notifier.send_status(SandboxStateEvent.SANDBOX_RUNNING)
    
    # Assert
    mock_websocket.send_json.assert_awaited_once_with({
        "event": "status_update",
        "status": "SANDBOX_RUNNING"
    })

@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.get_sandbox')
async def test_reconnect_and_stream(mock_get_sandbox):
    """
    Tests that a client can reconnect to a sandbox and continue streaming outputs.
    """
    # Arrange
    config = FakeSandboxConfig(executions=[ExecConfig()]) # To make is_execution_running true
    sandbox = FakeSandbox("test-sandbox", config=config)
    await sandbox.set_sandbox_token("test-token")
    mock_get_sandbox.return_value = sandbox

    output_stream = [
        {"type": "status_update", "status": "SANDBOX_EXECUTION_RUNNING"},
        {"type": OutputType.STDOUT, "data": "output1"},
        {"type": OutputType.STDERR, "data": "error1"},
        {"type": "status_update", "status": "SANDBOX_EXECUTION_DONE"},
    ]

    async def stream_outputs():
        for item in output_stream:
            yield item

    sandbox.stream_outputs = stream_outputs

    # Act & Assert
    with client.websocket_connect("/attach/test-sandbox?sandbox_token=test-token") as websocket:
        # Initial attach message
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}
        
        # Send reconnect and verify stream
        websocket.send_json({"action": "reconnect"})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_EXECUTION_RUNNING"}
        assert websocket.receive_json() == {"event": "stdout", "data": "output1"}
        assert websocket.receive_json() == {"event": "stderr", "data": "error1"}
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_EXECUTION_DONE"}

@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.create_sandbox')
async def test_handle_kill_process_success(mock_create_sandbox):
    """
    Tests that the 'kill_process' action is correctly handled.
    """
    # Arrange
    config = FakeSandboxConfig(executions=[ExecConfig()]) # To make is_execution_running true
    sandbox = FakeSandbox("test-sandbox", config=config)
    await sandbox.set_sandbox_token("test-token")
    sandbox.kill_exec_process = AsyncMock()
    mock_create_sandbox.return_value = sandbox

    # Act & Assert
    with client.websocket_connect("/create") as websocket:
        websocket.send_json({"idle_timeout": 120})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATING"}
        assert websocket.receive_json() == {
            "event": "sandbox_id",
            "sandbox_id": "test-sandbox",
            "sandbox_token": "test-token",
        }
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}

        websocket.send_json({"action": "kill_process"})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_EXECUTION_FORCE_KILLED"}

    sandbox.kill_exec_process.assert_awaited_once()

@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.create_sandbox')
async def test_handle_kill_process_not_running(mock_create_sandbox):
    """
    Tests that a kill request is handled gracefully if no process is running.
    """
    # Arrange
    sandbox = FakeSandbox("test-sandbox")
    await sandbox.set_sandbox_token("test-token")
    sandbox.kill_exec_process = AsyncMock()
    mock_create_sandbox.return_value = sandbox

    # Act & Assert
    with client.websocket_connect("/create") as websocket:
        websocket.send_json({"idle_timeout": 120})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATING"}
        assert websocket.receive_json() == {
            "event": "sandbox_id",
            "sandbox_id": "test-sandbox",
            "sandbox_token": "test-token",
        }
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}

        # Send kill request when no process is running
        websocket.send_json({"action": "kill_process"})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_EXECUTION_FORCE_KILLED"}

    sandbox.kill_exec_process.assert_not_awaited()

@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.create_sandbox')
async def test_handle_kill_process_failure(mock_create_sandbox):
    """
    Tests that a failure during killing is handled gracefully.
    """
    # Arrange
    config = FakeSandboxConfig(executions=[ExecConfig()]) # To make is_execution_running true
    sandbox = FakeSandbox("test-sandbox", config=config)
    await sandbox.set_sandbox_token("test-token")
    sandbox.kill_exec_process = AsyncMock(side_effect=Exception("Kill failed"))
    mock_create_sandbox.return_value = sandbox

    # Act & Assert
    with client.websocket_connect("/create") as websocket:
        websocket.send_json({"idle_timeout": 120})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATING"}
        assert websocket.receive_json() == {
            "event": "sandbox_id",
            "sandbox_id": "test-sandbox",
            "sandbox_token": "test-token",
        }
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}

        websocket.send_json({"action": "kill_process"})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_EXECUTION_FORCE_KILL_ERROR"}

    sandbox.kill_exec_process.assert_awaited_once()

@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.create_sandbox')
async def test_reconnect_and_stream_not_running(mock_create_sandbox):
    """
    Tests that a reconnecting client is correctly informed if the process has already finished.
    """
    # Arrange
    sandbox = FakeSandbox("test-sandbox")
    await sandbox.set_sandbox_token("test-token")
    mock_create_sandbox.return_value = sandbox

    # Act & Assert
    with client.websocket_connect("/create") as websocket:
        websocket.send_json({"idle_timeout": 120})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATING"}
        assert websocket.receive_json() == {
            "event": "sandbox_id",
            "sandbox_id": "test-sandbox",
            "sandbox_token": "test-token",
        }
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}

        # Send reconnect and verify stream
        websocket.send_json({"action": "reconnect"})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_EXECUTION_DONE"}

@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.create_sandbox')
@patch('src.sandbox.manager.SandboxManager.kill_sandbox')
async def test_handle_kill_sandbox_success(mock_kill_sandbox, mock_create_sandbox):
    """
    Tests that the 'kill_sandbox' action is correctly handled.
    """
    # Arrange
    sandbox = FakeSandbox("test-sandbox")
    await sandbox.set_sandbox_token("test-token")
    mock_create_sandbox.return_value = sandbox

    # Act & Assert
    with client.websocket_connect("/create") as websocket:
        websocket.send_json({"idle_timeout": 120})
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_CREATING"}
        assert websocket.receive_json() == {
            "event": "sandbox_id",
            "sandbox_id": "test-sandbox",
            "sandbox_token": "test-token",
        }
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_RUNNING"}

        websocket.send_json({"action": "kill_sandbox"})
        with pytest.raises(WebSocketDisconnect) as e:
            websocket.receive_json()
        assert e.value.code == 1000

    mock_kill_sandbox.assert_awaited_once_with("test-sandbox")

@pytest.mark.asyncio
@patch('src.sandbox.manager.SandboxManager.get_sandbox')
async def test_attach_with_invalid_token(mock_get_sandbox):
    """
    Tests that attaching to a sandbox with an invalid token fails.
    """
    # Arrange
    sandbox = FakeSandbox("test-sandbox")
    await sandbox.set_sandbox_token("correct-token")
    mock_get_sandbox.return_value = sandbox

    # Act & Assert
    with client.websocket_connect("/attach/test-sandbox?sandbox_token=incorrect-token") as websocket:
        assert websocket.receive_json() == {"event": "status_update", "status": "SANDBOX_PERMISSION_DENIAL_ERROR"}
        assert websocket.receive_json() == {"event": "error", "message": "Invalid sandbox token."}
        with pytest.raises(WebSocketDisconnect) as e:
            websocket.receive_json()
        assert e.value.code == 4000

def test_attach_with_missing_token():
    """
    Tests that attaching to a sandbox without a token query param fails the
    websocket handshake.
    """
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/attach/test-sandbox"):
            pass  # The connection should fail immediately
    # FastAPI/Starlette closes with 1000 and sends a 422/403 before the connection is established from the client's perspective.
    # The test client reflects this as a disconnect. The exact code can vary.
    # We are primarily interested in the fact that it disconnects.
    assert e.type == WebSocketDisconnect
