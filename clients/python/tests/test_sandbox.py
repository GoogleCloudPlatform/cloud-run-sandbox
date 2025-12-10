
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

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from sandbox.sandbox import Sandbox
from sandbox.exceptions import SandboxCreationError, SandboxConnectionError, SandboxStateError, SandboxExecutionError, SandboxFilesystemSnapshotError, SandboxCheckpointError
from sandbox.types import MessageKey, EventType, SandboxEvent

@pytest.fixture
def mock_connection_factory():
    """
    A pytest fixture that provides a factory for creating a mocked Connection.
    It patches `sandbox.sandbox.Connection` and allows tests to simulate
    the connection's behavior by invoking the callbacks that the Sandbox provides.
    """
    with patch('sandbox.sandbox.Connection', new_callable=MagicMock) as mock_Connection:
        
        async def _factory(creation_messages, exec_messages_list=None, close_on_finish=True):
            if exec_messages_list is None:
                exec_messages_list = []

            mock_conn_instance = AsyncMock()
            mock_Connection.return_value = mock_conn_instance

            on_message_callback = None
            on_close_callback = None

            def side_effect(url, on_message, on_error, on_close, **kwargs):
                nonlocal on_message_callback, on_close_callback
                on_message_callback = on_message
                on_close_callback = on_close
                return mock_conn_instance
            
            mock_Connection.side_effect = side_effect

            async def simulate_messages():
                await asyncio.sleep(0) # Allow the Sandbox.create to run first
                for msg in creation_messages:
                    on_message_callback(json.dumps(msg))
                    await asyncio.sleep(0)

                if not exec_messages_list and close_on_finish:
                    on_close_callback(1000, "Normal close")

            message_task = asyncio.create_task(simulate_messages())

            send_count = 0
            async def send_side_effect(message):
                nonlocal send_count
                msg_data = json.loads(message)
                
                if "code" in msg_data or "action" in msg_data:
                    if send_count < len(exec_messages_list):
                        exec_messages = exec_messages_list[send_count]
                        send_count += 1
                        for msg in exec_messages:
                            on_message_callback(json.dumps(msg))
                            await asyncio.sleep(0)
                        
                        if send_count == len(exec_messages_list) and close_on_finish:
                            on_close_callback(1000, "Normal close")
            
            mock_conn_instance.send.side_effect = send_side_effect
            
            return mock_conn_instance, message_task

        yield _factory

@pytest.mark.asyncio
async def test_sandbox_create_and_kill(mock_connection_factory):
    """
    Tests that a sandbox can be created and killed without errors.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    mock_conn, msg_task = await mock_connection_factory(creation_messages)

    # Act
    sandbox = await Sandbox.create("ws://test")
    
    # Assert (Creation)
    assert sandbox.sandbox_id == "test_id"
    assert sandbox.sandbox_token == "test_token"
    
    # Act (Termination)
    await sandbox.kill(timeout=0.1)

    # Assert (Termination)
    mock_conn.close.assert_awaited_once()
    await msg_task # Ensure simulation is complete

@pytest.mark.asyncio
async def test_sandbox_create_failure(mock_connection_factory):
    """
    Tests that Sandbox.create raises a SandboxCreationError on failure.
    """
    # Arrange
    error_messages = [
        {
            MessageKey.EVENT: EventType.STATUS_UPDATE,
            MessageKey.STATUS: SandboxEvent.SANDBOX_CREATION_ERROR,
            MessageKey.MESSAGE: "Failed to create sandbox"
        },
    ]
    _, msg_task = await mock_connection_factory(error_messages)

    # Act & Assert
    with pytest.raises(SandboxCreationError, match="Failed to create sandbox"):
        await Sandbox.create("ws://test")
    await msg_task

@pytest.mark.asyncio
async def test_sandbox_connection_lost_during_creation(mock_connection_factory):
    """
    Tests that a connection lost during creation raises an error.
    """
    # Arrange
    # An empty message list will cause the on_close callback to be called immediately.
    _, msg_task = await mock_connection_factory([])

    # Act & Assert
    with pytest.raises(SandboxConnectionError, match="Connection closed:"):
        await Sandbox.create("ws://test")
    await msg_task

@pytest.mark.asyncio
async def test_sandbox_exec_dispatches_messages(mock_connection_factory):
    """
    Tests that the sandbox correctly dispatches messages in response to exec.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    exec_messages = [
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_EXECUTION_RUNNING},
        {MessageKey.EVENT: EventType.STDOUT, MessageKey.DATA: "output"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_EXECUTION_DONE},
    ]
    _, msg_task = await mock_connection_factory(creation_messages, [exec_messages])
    
    sandbox = await Sandbox.create("ws://test")
    
    # Act
    process = await sandbox.exec("bash", "command")    
    # Assert
    output = await process.stdout.read_all()
    assert output == "output"
    await process.wait()
    await sandbox.kill(timeout=0.1)
    await msg_task

@pytest.mark.asyncio
async def test_can_exec_sequentially(mock_connection_factory):
    """
    Tests that multiple processes can be executed one after another.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    exec_messages_1 = [
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_EXECUTION_RUNNING},
        {MessageKey.EVENT: EventType.STDOUT, MessageKey.DATA: "output1"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_EXECUTION_DONE},
    ]
    exec_messages_2 = [
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_EXECUTION_RUNNING},
        {MessageKey.EVENT: EventType.STDOUT, MessageKey.DATA: "output2"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_EXECUTION_DONE},
    ]
    
    _, msg_task = await mock_connection_factory(creation_messages, [exec_messages_1, exec_messages_2])
    
    sandbox = await Sandbox.create("ws://test")

    # Act & Assert for first process
    process1 = await sandbox.exec("command1", "bash")
    output1 = await process1.stdout.read_all()
    assert output1 == "output1"
    await process1.wait()

    # Act & Assert for second process
    process2 = await sandbox.exec("command2", "bash")
    output2 = await process2.stdout.read_all()
    assert output2 == "output2"
    await process2.wait()

    await sandbox.kill(timeout=0.1)
    await msg_task

@pytest.mark.asyncio
async def test_cannot_exec_multiple_processes_concurrently(mock_connection_factory):
    """
    Tests that exec raises an error if a process is already running.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    exec_messages = [
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_EXECUTION_RUNNING},
        {MessageKey.EVENT: EventType.STDOUT, MessageKey.DATA: "output"},
    ]
    
    _, msg_task = await mock_connection_factory(creation_messages, [exec_messages], close_on_finish=False)
    
    sandbox = await Sandbox.create("ws://test")

    # Act & Assert
    await sandbox.exec("command1", "bash")
    
    with pytest.raises(RuntimeError, match="Another process is already running"):
        await sandbox.exec("command2", "bash")

    await sandbox.kill(timeout=0.1)
    await msg_task

@pytest.mark.asyncio
async def test_listen_task_is_cancelled_on_kill(mock_connection_factory):
    """
    Tests that killing the sandbox results in the underlying connection being closed.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    mock_conn, msg_task = await mock_connection_factory(creation_messages, close_on_finish=False)
    sandbox = await Sandbox.create("ws://test")

    # Act
    await sandbox.kill(timeout=0.1)

    # Assert
    mock_conn.close.assert_awaited_once()
    await msg_task

@pytest.mark.asyncio
async def test_exec_raises_error_if_not_running(mock_connection_factory):
    """
    Tests that exec raises a SandboxStateError if the sandbox is not running.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    _, msg_task = await mock_connection_factory(creation_messages, close_on_finish=False)
    
    sandbox = await Sandbox.create("ws://test")
    await sandbox.kill(timeout=0.1)

    # Act & Assert
    with pytest.raises(SandboxStateError, match="Sandbox is not in a running state. Current state: closed"):
        await sandbox.exec("command", "bash")
    await msg_task

@pytest.mark.asyncio
async def test_unsupported_language_error_raises_exception(mock_connection_factory):
    """
    Tests that a SandboxExecutionError is raised for unsupported language errors.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    exec_messages = [
        {
            MessageKey.EVENT: EventType.STATUS_UPDATE,
            MessageKey.STATUS: SandboxEvent.SANDBOX_EXECUTION_UNSUPPORTED_LANGUAGE_ERROR,
            MessageKey.MESSAGE: "Unsupported language: javascript"
        },
    ]
    _, msg_task = await mock_connection_factory(creation_messages, [exec_messages])
    
    sandbox = await Sandbox.create("ws://test")
    
    # Act & Assert
    with pytest.raises(SandboxExecutionError, match="Unsupported language: javascript"):
        await sandbox.exec("javascript", "console.log('hello')")
    
    await sandbox.kill(timeout=0.1)
    await msg_task

@pytest.mark.asyncio
async def test_debug_logging(mock_connection_factory, capsys):
    """
    Tests that debug logs are correctly generated.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    exec_messages = [
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_EXECUTION_RUNNING},
        {MessageKey.EVENT: EventType.STDOUT, MessageKey.DATA: "output"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_EXECUTION_DONE},
    ]
    
    # --- Test with debug enabled ---
    _, msg_task = await mock_connection_factory(creation_messages, [exec_messages], close_on_finish=False)
    sandbox_debug = await Sandbox.create("ws://test", enable_debug=True, debug_label="TestLabel")
    process = await sandbox_debug.exec("bash", "command")
    await process.wait()
    await sandbox_debug.kill(timeout=0.1)

    captured_debug = capsys.readouterr()
    
    assert "[TestLabel] Received message: {\"event\": \"sandbox_id\", \"sandbox_id\": \"test_id\", \"sandbox_token\": \"test_token\"}" in captured_debug.out
    assert "[TestLabel] Received message: {\"event\": \"status_update\", \"status\": \"SANDBOX_RUNNING\"}" in captured_debug.out
    assert "STDOUT" not in captured_debug.out
    await msg_task

@pytest.mark.asyncio
async def test_sandbox_attach_success(mock_connection_factory):
    """
    Tests that a sandbox can be attached to successfully.
    """
    # Arrange
    attach_messages = [
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    mock_conn, msg_task = await mock_connection_factory(attach_messages)

    # Act
    sandbox = await Sandbox.attach("ws://test", "existing_id", "test_token")
    
    # Assert
    assert sandbox.sandbox_id == "existing_id"
    assert sandbox.sandbox_token == "test_token"
    
    await sandbox.kill(timeout=0.1)
    mock_conn.close.assert_awaited_once()
    await msg_task

@pytest.mark.asyncio
async def test_sandbox_attach_not_found(mock_connection_factory):
    """
    Tests that attach raises SandboxCreationError if the sandbox is not found.
    """
    # Arrange
    error_messages = [
        {
            MessageKey.EVENT: EventType.STATUS_UPDATE,
            MessageKey.STATUS: SandboxEvent.SANDBOX_NOT_FOUND,
            MessageKey.MESSAGE: "Sandbox not found"
        },
    ]
    _, msg_task = await mock_connection_factory(error_messages)

    # Act & Assert
    with pytest.raises(SandboxCreationError, match="Sandbox not found"):
        await Sandbox.attach("ws://test", "non_existent_id", "test_token")
    await msg_task

@pytest.mark.asyncio
async def test_sandbox_attach_in_use(mock_connection_factory):
    """
    Tests that attach raises SandboxCreationError if the sandbox is in use.
    """
    # Arrange
    error_messages = [
        {
            MessageKey.EVENT: EventType.STATUS_UPDATE,
            MessageKey.STATUS: SandboxEvent.SANDBOX_IN_USE,
            MessageKey.MESSAGE: "Sandbox in use"
        },
    ]
    _, msg_task = await mock_connection_factory(error_messages)

    # Act & Assert
    with pytest.raises(SandboxCreationError, match="Sandbox in use"):
        await Sandbox.attach("ws://test", "in_use_id", "test_token")
    await msg_task

@pytest.mark.asyncio
async def test_sandbox_attach_restore_error(mock_connection_factory):
    """
    Tests that attach raises SandboxCreationError if a restore error occurs.
    """
    # Arrange
    error_messages = [
        {
            MessageKey.EVENT: EventType.STATUS_UPDATE,
            MessageKey.STATUS: SandboxEvent.SANDBOX_RESTORE_ERROR,
            MessageKey.MESSAGE: "Failed to restore sandbox"
        },
    ]
    _, msg_task = await mock_connection_factory(error_messages)

    # Act & Assert
    with pytest.raises(SandboxCreationError, match="Failed to restore sandbox"):
        await Sandbox.attach("ws://test", "any_id", "test_token")
    await msg_task

@pytest.mark.asyncio
async def test_sandbox_attach_restore_error_2(mock_connection_factory):
    """
    Tests that attach raises SandboxCreationError if a restore error occurs.
    """
    # Arrange
    error_messages = [
        {
            MessageKey.EVENT: EventType.STATUS_UPDATE,
            MessageKey.STATUS: SandboxEvent.SANDBOX_RESTORE_ERROR,
            MessageKey.MESSAGE: "Failed to restore sandbox"
        },
    ]
    _, msg_task = await mock_connection_factory(error_messages)

    # Act & Assert
    with pytest.raises(SandboxCreationError, match="Failed to restore sandbox"):
        await Sandbox.attach("ws://test", "any_id", "test_token")
    await msg_task


@pytest.mark.asyncio
async def test_sandbox_attach_permission_denial(mock_connection_factory):
    """
    Tests that attach raises SandboxCreationError if the server denies the request.
    """
    # Arrange
    error_messages = [
        {
            MessageKey.EVENT: EventType.STATUS_UPDATE,
            MessageKey.STATUS: SandboxEvent.SANDBOX_PERMISSION_DENIAL_ERROR,
            MessageKey.MESSAGE: "Invalid sandbox token"
        },
    ]
    _, msg_task = await mock_connection_factory(error_messages)

    # Act & Assert
    with pytest.raises(SandboxCreationError, match="Invalid sandbox token"):
        await Sandbox.attach("ws://test", "any_id", "wrong_token")
    await msg_task


@pytest.mark.asyncio
async def test_sandbox_connection_lost_during_attach(mock_connection_factory):
    """
    Tests that a connection lost during attach raises a SandboxConnectionError.
    """
    # Arrange
    _, msg_task = await mock_connection_factory([])

    # Act & Assert
    with pytest.raises(SandboxConnectionError, match="Connection closed:"):
        await Sandbox.attach("ws://test", "any_id", "test_token")
    await msg_task

@pytest.mark.asyncio
async def test_sandbox_kill_sends_kill_action(mock_connection_factory):
    """
    Tests that calling kill() on a sandbox sends the correct kill_sandbox action.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    mock_conn, msg_task = await mock_connection_factory(creation_messages, close_on_finish=False)
    sandbox = await Sandbox.create("ws://test")

    # Act
    await sandbox.kill()

    # Assert
    mock_conn.send.assert_any_call(json.dumps({"action": "kill_sandbox"}))
    await msg_task

@pytest.mark.asyncio
async def test_sandbox_kill_unblocks_on_server_messages(mock_connection_factory):
    """
    Tests that kill() is unblocked when SANDBOX_KILLED is received.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    kill_messages = [
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_KILLED}
    ]
    
    mock_conn, msg_task = await mock_connection_factory(creation_messages, [kill_messages], close_on_finish=False)
    sandbox = await Sandbox.create("ws://test")

    # Act
    await sandbox.kill()

    # Assert
    assert sandbox._state == "closed"
    mock_conn.send.assert_any_call(json.dumps({"action": "kill_sandbox"}))
    await msg_task

@pytest.mark.asyncio
async def test_sandbox_kill_timeout_returns(mock_connection_factory):
    """
    Tests that kill() returns via timeout if no confirmation is received.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    # No kill confirmation messages are scripted
    mock_conn, msg_task = await mock_connection_factory(creation_messages, [], close_on_finish=False)
    sandbox = await Sandbox.create("ws://test")

    # Act
    await sandbox.kill(timeout=0.1)

    # Assert
    assert sandbox._state == "closed"
    mock_conn.send.assert_any_call(json.dumps({"action": "kill_sandbox"}))
    await msg_task

@pytest.mark.asyncio
async def test_sandbox_kill_is_idempotent(mock_connection_factory):
    """
    Tests that calling kill() multiple times does not cause errors.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    mock_conn, msg_task = await mock_connection_factory(creation_messages, close_on_finish=False)
    sandbox = await Sandbox.create("ws://test")

    # Act
    await sandbox.kill()
    await sandbox.kill()

    # Assert
    assert sandbox._state == "closed"
    await msg_task

@pytest.mark.asyncio
async def test_snapshot_filesystem_raises_error_if_not_running(mock_connection_factory):
    """
    Tests that snapshot_filesystem raises a SandboxStateError if the sandbox is not in the 'running' state.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    _, msg_task = await mock_connection_factory(creation_messages, close_on_finish=False)
    
    sandbox = await Sandbox.create("ws://test")
    await sandbox.kill(timeout=0.1) # Terminate the sandbox to put it in a non-running state.

    # Act & Assert
    with pytest.raises(SandboxStateError, match="Sandbox is not in a running state. Current state: closed"):
        await sandbox.snapshot_filesystem("test_snapshot")
    await msg_task

@pytest.mark.asyncio
async def test_snapshot_filesystem_raises_error_on_failure(mock_connection_factory):
    """
    Tests that snapshot_filesystem raises a SandboxFilesystemSnapshotError if the server sends a SANDBOX_FILESYSTEM_SNAPSHOT_ERROR event.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    snapshot_messages = [
        {
            MessageKey.EVENT: EventType.STATUS_UPDATE,
            MessageKey.STATUS: SandboxEvent.SANDBOX_FILESYSTEM_SNAPSHOT_ERROR,
            MessageKey.MESSAGE: "Snapshot failed"
        },
    ]
    _, msg_task = await mock_connection_factory(creation_messages, [snapshot_messages], close_on_finish=False)
    
    sandbox = await Sandbox.create("ws://test")

    # Act & Assert
    with pytest.raises(SandboxFilesystemSnapshotError, match="Snapshot failed"):
        await sandbox.snapshot_filesystem("test_snapshot")
    
    await sandbox.kill(timeout=0.1)
    await msg_task

@pytest.mark.asyncio
async def test_create_sends_filesystem_snapshot_name(mock_connection_factory):
    """
    Tests that create sends the filesystem_snapshot_name parameter.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    mock_conn, msg_task = await mock_connection_factory(creation_messages, close_on_finish=False)

    # Act
    sandbox = await Sandbox.create("ws://test", filesystem_snapshot_name="test_snapshot")

    # Assert
    mock_conn.send.assert_any_call(json.dumps({"idle_timeout": 60, "filesystem_snapshot_name": "test_snapshot"}))
    await sandbox.kill(timeout=0.1)
    await msg_task

@pytest.mark.asyncio
async def test_create_sends_enable_checkpoint(mock_connection_factory):
    """
    Tests that create sends the enable_checkpoint parameter.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    mock_conn, msg_task = await mock_connection_factory(creation_messages, close_on_finish=False)

    # Act
    sandbox = await Sandbox.create("ws://test", enable_sandbox_checkpoint=True)

    # Assert
    mock_conn.send.assert_any_call(json.dumps({"idle_timeout": 60, "enable_checkpoint": True}))
    await sandbox.kill(timeout=0.1)
    await msg_task

@pytest.mark.asyncio
async def test_create_sends_enable_idle_timeout_auto_checkpoint(mock_connection_factory):
    """
    Tests that create sends the enable_idle_timeout_auto_checkpoint parameter.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    mock_conn, msg_task = await mock_connection_factory(creation_messages, close_on_finish=False)

    # Act
    sandbox = await Sandbox.create("ws://test", enable_idle_timeout_auto_checkpoint=True)

    # Assert
    mock_conn.send.assert_any_call(json.dumps({"idle_timeout": 60, "enable_idle_timeout_auto_checkpoint": True}))
    await sandbox.kill(timeout=0.1)
    await msg_task

@pytest.mark.asyncio
async def test_create_sends_enable_sandbox_handoff(mock_connection_factory):
    """
    Tests that create sends the enable_sandbox_handoff parameter.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    mock_conn, msg_task = await mock_connection_factory(creation_messages, close_on_finish=False)

    # Act
    sandbox = await Sandbox.create("ws://test", enable_sandbox_handoff=True)

    # Assert
    mock_conn.send.assert_any_call(json.dumps({"idle_timeout": 60, "enable_sandbox_handoff": True}))
    await sandbox.kill(timeout=0.1)
    await msg_task

@pytest.mark.asyncio
async def test_checkpoint_raises_error_if_not_running(mock_connection_factory):
    """
    Tests that checkpoint raises a SandboxStateError if the sandbox is not in the 'running' state.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    _, msg_task = await mock_connection_factory(creation_messages, close_on_finish=False)
    
    sandbox = await Sandbox.create("ws://test")
    await sandbox.kill(timeout=0.1) # Terminate the sandbox to put it in a non-running state.

    # Act & Assert
    with pytest.raises(SandboxStateError, match="Sandbox is not in a running state. Current state: closed"):
        await sandbox.checkpoint()
    await msg_task

@pytest.mark.asyncio
async def test_checkpoint_raises_error_on_failure(mock_connection_factory):
    """
    Tests that checkpoint raises a SandboxCheckpointError if the server sends a SANDBOX_CHECKPOINT_ERROR event.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    checkpoint_messages = [
        {
            MessageKey.EVENT: EventType.STATUS_UPDATE,
            MessageKey.STATUS: SandboxEvent.SANDBOX_CHECKPOINT_ERROR,
            MessageKey.MESSAGE: "Checkpoint failed"
        },
    ]
    _, msg_task = await mock_connection_factory(creation_messages, [checkpoint_messages], close_on_finish=False)
    
    sandbox = await Sandbox.create("ws://test")

    # Act & Assert
    with pytest.raises(SandboxCheckpointError, match="Checkpoint failed"):
        await sandbox.checkpoint()
    
    await sandbox.kill(timeout=0.1)
    await msg_task

@pytest.mark.asyncio
async def test_checkpoint_raises_error_if_execution_in_progress(mock_connection_factory):
    """
    Tests that checkpoint raises a SandboxCheckpointError if an execution is in progress.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    checkpoint_messages = [
        {
            MessageKey.EVENT: EventType.STATUS_UPDATE,
            MessageKey.STATUS: SandboxEvent.SANDBOX_EXECUTION_IN_PROGRESS_ERROR,
            MessageKey.MESSAGE: "Execution in progress"
        },
    ]
    _, msg_task = await mock_connection_factory(
        creation_messages,
        [checkpoint_messages],
        close_on_finish=False
    )

    sandbox = await Sandbox.create("ws://test")

    # Act & Assert
    with pytest.raises(SandboxCheckpointError, match="Execution in progress"):
        await sandbox.checkpoint()

    # The state should be back to running
    assert sandbox._state == "running"

    await sandbox.kill(timeout=0.1)
    await msg_task





@pytest.mark.asyncio
async def test_snapshot_filesystem_success(mock_connection_factory):
    """
    Tests that snapshot_filesystem sends the correct message and waits for the event.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    snapshot_messages = [
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_FILESYSTEM_SNAPSHOT_CREATED},
    ]
    mock_conn, msg_task = await mock_connection_factory(creation_messages, [snapshot_messages], close_on_finish=False)
    
    sandbox = await Sandbox.create("ws://test")

    # Act
    await sandbox.snapshot_filesystem("test_snapshot")

    # Assert
    mock_conn.send.assert_any_call(json.dumps({"action": "snapshot_filesystem", "name": "test_snapshot"}))
    assert sandbox._state == "running"
    await sandbox.kill(timeout=0.1)
    await msg_task

@pytest.mark.asyncio
async def test_checkpoint_success(mock_connection_factory):
    """
    Tests that checkpoint sends the correct message and waits for the event.
    """
    # Arrange
    creation_messages = [
        {MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"},
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING},
    ]
    checkpoint_messages = [
        {MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_CHECKPOINTED},
    ]
    mock_conn, msg_task = await mock_connection_factory(creation_messages, [checkpoint_messages], close_on_finish=False)
    
    sandbox = await Sandbox.create("ws://test")

    # Act
    await sandbox.checkpoint()

    # Assert
    mock_conn.send.assert_any_call(json.dumps({"action": "checkpoint"}))
    assert sandbox._state == "closed"
    await msg_task

@pytest.mark.asyncio
@patch('sandbox.sandbox.Connection')
async def test_sandbox_provides_should_reconnect_callback(mock_Connection):
    """
    Tests that the Sandbox provides a `should_reconnect` callback that
    correctly reflects the sandbox state.
    """
    # Arrange
    mock_conn_instance = AsyncMock()
    mock_Connection.return_value = mock_conn_instance
    
    captured_callbacks = {}
    on_message_callback = None

    # This side effect captures the callbacks. The test will manually send the
    # messages needed to unblock the Sandbox.create() call.
    def side_effect(url, on_message, on_error, on_close, should_reconnect, get_reconnect_info, on_reopen, **kwargs):
        nonlocal on_message_callback
        on_message_callback = on_message
        captured_callbacks['should_reconnect'] = should_reconnect
        return mock_conn_instance
    
    mock_Connection.side_effect = side_effect

    # Act: Create the sandbox. This will hang until we send messages.
    create_task = asyncio.create_task(Sandbox.create("ws://test", enable_auto_reconnect=True))
    
    # Allow create_task to run and set the on_message_callback
    await asyncio.sleep(0) 
    
    # Simulate server messages to get sandbox to "running" state, unblocking create()
    on_message_callback(json.dumps({MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"}))
    await asyncio.sleep(0)
    on_message_callback(json.dumps({MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING}))
    
    sandbox = await create_task

    # Assert
    should_reconnect_cb = captured_callbacks['should_reconnect']
    
    # 1. Should reconnect on abnormal closure when running
    assert sandbox._state == "running"
    assert should_reconnect_cb(1006, "Abnormal closure") is True
    
    # 2. Should NOT reconnect if kill was intentional
    sandbox._is_kill_intentionally = True
    assert should_reconnect_cb(1006, "Abnormal closure") is False
    sandbox._is_kill_intentionally = False

    # 3. Should NOT reconnect on fatal errors
    sandbox._update_should_reconnect(SandboxEvent.SANDBOX_NOT_FOUND)
    assert should_reconnect_cb(1006, "Abnormal closure") is False
    
    # 4. Should reconnect again if state is back to running
    sandbox._update_should_reconnect(SandboxEvent.SANDBOX_RUNNING)
    assert should_reconnect_cb(1006, "Abnormal closure") is True

    await sandbox.kill(timeout=0.1)


@pytest.mark.asyncio
@patch('sandbox.sandbox.Connection')
async def test_sandbox_provides_get_reconnect_info_callback(mock_Connection):
    """
    Tests that the Sandbox provides a `get_reconnect_info` callback that
    returns the correct URL for reconnection.
    """
    # Arrange
    mock_conn_instance = AsyncMock()
    mock_Connection.return_value = mock_conn_instance
    
    captured_callbacks = {}
    on_message_callback = None

    def side_effect(url, on_message, on_error, on_close, should_reconnect, get_reconnect_info, on_reopen, **kwargs):
        nonlocal on_message_callback
        on_message_callback = on_message
        captured_callbacks['get_reconnect_info'] = get_reconnect_info
        return mock_conn_instance
    
    mock_Connection.side_effect = side_effect

    create_task = asyncio.create_task(Sandbox.create("ws://test", enable_auto_reconnect=True))
    await asyncio.sleep(0)
    on_message_callback(json.dumps({MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"}))
    await asyncio.sleep(0)
    on_message_callback(json.dumps({MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING}))
    sandbox = await create_task

    # Act
    get_reconnect_info_cb = captured_callbacks['get_reconnect_info']
    reconnect_info = get_reconnect_info_cb()

    # Assert
    assert reconnect_info['url'] == "ws://test/attach/test_id?sandbox_token=test_token"

    await sandbox.kill(timeout=0.1)

@pytest.mark.asyncio
@patch('sandbox.sandbox.Connection')
async def test_sandbox_provides_on_reopen_callback(mock_Connection):
    """
    Tests that the Sandbox provides an `on_reopen` callback that sends the
    correct reconnect message.
    """
    # Arrange
    mock_conn_instance = AsyncMock()
    mock_Connection.return_value = mock_conn_instance
    
    captured_callbacks = {}
    on_message_callback = None

    def side_effect(url, on_message, on_error, on_close, should_reconnect, get_reconnect_info, on_reopen, **kwargs):
        nonlocal on_message_callback
        on_message_callback = on_message
        captured_callbacks['on_reopen'] = on_reopen
        return mock_conn_instance
    
    mock_Connection.side_effect = side_effect

    create_task = asyncio.create_task(Sandbox.create("ws://test", enable_auto_reconnect=True))
    await asyncio.sleep(0)
    on_message_callback(json.dumps({MessageKey.EVENT: EventType.SANDBOX_ID, MessageKey.SANDBOX_ID: "test_id", MessageKey.SANDBOX_TOKEN: "test_token"}))
    await asyncio.sleep(0)
    on_message_callback(json.dumps({MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING}))
    sandbox = await create_task

    # Act
    on_reopen_cb = captured_callbacks['on_reopen']
    await on_reopen_cb()

    # Assert
    mock_conn_instance.send.assert_called_with(json.dumps({"action": "reconnect"}))

    await sandbox.kill(timeout=0.1)


@pytest.mark.asyncio
@patch('sandbox.sandbox.Connection')
async def test_sandbox_attach_provides_reconnect_callbacks(mock_Connection):
    """
    Tests that the Sandbox, when attached, provides the correct interface
    (callbacks) for auto-reconnect functionality.
    """
    # Arrange
    mock_conn_instance = AsyncMock()
    mock_Connection.return_value = mock_conn_instance
    
    captured_callbacks = {}
    on_message_callback = None

    def side_effect(url, on_message, on_error, on_close, should_reconnect, get_reconnect_info, on_reopen, **kwargs):
        nonlocal on_message_callback
        on_message_callback = on_message
        captured_callbacks['should_reconnect'] = should_reconnect
        captured_callbacks['get_reconnect_info'] = get_reconnect_info
        captured_callbacks['on_reopen'] = on_reopen
        return mock_conn_instance
    
    mock_Connection.side_effect = side_effect

    # Act: Attach to the sandbox
    attach_task = asyncio.create_task(Sandbox.attach("ws://test", "test_id", "test_token", enable_auto_reconnect=True))
    
    await asyncio.sleep(0)
    on_message_callback(json.dumps({MessageKey.EVENT: EventType.STATUS_UPDATE, MessageKey.STATUS: SandboxEvent.SANDBOX_RUNNING}))
    
    sandbox = await attach_task

    # Assert
    # 1. Test `should_reconnect` callback
    should_reconnect_cb = captured_callbacks['should_reconnect']
    assert should_reconnect_cb(1006, "Abnormal closure") is True

    # 2. Test `get_reconnect_info` callback
    get_reconnect_info_cb = captured_callbacks['get_reconnect_info']
    reconnect_info = get_reconnect_info_cb()
    assert reconnect_info['url'] == "ws://test/attach/test_id?sandbox_token=test_token"

    # 3. Test `on_reopen` callback
    on_reopen_cb = captured_callbacks['on_reopen']
    await on_reopen_cb()
    mock_conn_instance.send.assert_called_with(json.dumps({"action": "reconnect"}))

    await sandbox.kill(timeout=0.1)

