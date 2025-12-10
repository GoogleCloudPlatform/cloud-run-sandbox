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
import asyncio
import os
import tempfile
from unittest.mock import AsyncMock
from src.sandbox.fake import FakeSandbox, FakeSandboxConfig, ExecConfig
from src.sandbox.interface import SandboxCreationError, SandboxOperationError, SandboxSnapshotFilesystemError, SandboxStreamClosed
from src.sandbox.types import SandboxOutputEvent, OutputType, CodeLanguage, SandboxStateEvent

pytestmark = pytest.mark.asyncio

async def test_fake_sandbox_lifecycle_and_output():
    """
    Tests the full lifecycle and output streaming of the FakeSandbox.
    """
    # Configure the fake sandbox to produce a specific stream of events
    output_stream: list[SandboxOutputEvent] = [
        {"type": OutputType.STDOUT, "data": "line 1"},
        {"type": OutputType.STDERR, "data": "error 1"},
        {"type": OutputType.STDOUT, "data": "line 2"},
    ]
    config = FakeSandboxConfig(executions=[ExecConfig(output_stream=output_stream)])
    sandbox = FakeSandbox("fake-123", config=config)

    # 1. Create and start
    await sandbox.create()
    await sandbox.execute(CodeLanguage.PYTHON, "some code")

    # 2. Verify the output stream
    messages = []
    try:
        async for msg in sandbox.stream_outputs():
            messages.append(msg)
    except SandboxStreamClosed:
        pass  # Expected at the end of the stream

    # Convert the received messages for comparison
    received_messages = [{"type": msg.get("type"), "data": msg.get("data"), "status": msg.get("status")} for msg in messages]
    expected_messages = [
        {"type": "status_update", "status": "SANDBOX_EXECUTION_RUNNING", "data": None},
        {"type": OutputType.STDOUT, "data": "line 1", "status": None},
        {"type": OutputType.STDERR, "data": "error 1", "status": None},
        {"type": OutputType.STDOUT, "data": "line 2", "status": None},
        {"type": "status_update", "status": "SANDBOX_EXECUTION_DONE", "data": None},
    ]

    assert received_messages == expected_messages
    
    await sandbox.delete()

async def test_fake_sandbox_create_error():
    """
    Tests that the FakeSandbox correctly raises a SandboxCreationError.
    """
    config = FakeSandboxConfig(create_should_fail=True)
    sandbox = FakeSandbox("fake-error", config=config)
    
    with pytest.raises(SandboxCreationError):
        await sandbox.create()

async def test_fake_sandbox_is_attached():
    """
    Tests the is_attached property of the FakeSandbox.
    """
    sandbox = FakeSandbox("fake-123")
    assert not sandbox.is_attached
    sandbox.is_attached = True
    assert sandbox.is_attached

async def test_fake_sandbox_delete():
    """
    Tests that the delete method works.
    """
    sandbox = FakeSandbox("fake-123")
    await sandbox.create()
    await sandbox.delete()

async def test_fake_sandbox_write_stdin():
    """
    Tests that the write_stdin method correctly validates the input.
    """
    config = FakeSandboxConfig(executions=[ExecConfig(expected_stdin=["hello\n"])])
    sandbox = FakeSandbox("fake-123", config=config)

    await sandbox.create()
    await sandbox.execute(CodeLanguage.PYTHON, "some code")

    await sandbox.write_stdin("hello\n")

    with pytest.raises(AssertionError):
        await sandbox.write_stdin("world\n")

async def test_fake_sandbox_checkpoint_and_restore():
    """
    Tests that the FakeSandbox can be checkpointed and restored successfully.
    """
    sandbox = FakeSandbox("fake-checkpoint")
    with tempfile.TemporaryDirectory() as tmpdir:
        checkpoint_path = tmpdir

        # Checkpoint should create the file
        await sandbox.create()
        await sandbox.checkpoint(checkpoint_path)
        assert os.path.exists(os.path.join(checkpoint_path, "checkpoint.img"))

        # Restore should succeed
        sandbox2 = FakeSandbox("fake-restore")
        await sandbox2.restore(checkpoint_path)

async def test_fake_sandbox_checkpoint_fails_if_running():
    """
    Tests that checkpointing fails if the sandbox is in the middle of an execution.
    """
    config = FakeSandboxConfig(executions=[ExecConfig()])
    sandbox = FakeSandbox("fake-checkpoint-fail", config=config)
    await sandbox.create()
    await sandbox.execute(CodeLanguage.PYTHON, "code")

    with pytest.raises(SandboxOperationError, match="Cannot checkpoint while an execution is in progress."):
        await sandbox.checkpoint("/tmp/dummy_path")

async def test_fake_sandbox_restore_fails_if_no_checkpoint():
    """
    Tests that restoring fails if the checkpoint file does not exist.
    """
    sandbox = FakeSandbox("fake-restore-fail")
    non_existent_path = "/tmp/non_existent_checkpoint"
    assert not os.path.exists(non_existent_path)

    with pytest.raises(SandboxOperationError, match=f"Checkpoint file not found at {non_existent_path}"):
        await sandbox.restore(non_existent_path)

async def test_fake_sandbox_snapshot_filesystem():
    """
    Tests that the FakeSandbox can be snapshotted and that it can fail.
    """
    # Test successful snapshot
    sandbox = FakeSandbox("fake-snapshot")
    await sandbox.create()
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        snapshot_path = tmp.name
    await sandbox.snapshot_filesystem(snapshot_path)
    os.remove(snapshot_path)

    # Test failed snapshot
    config = FakeSandboxConfig(snapshot_filesystem_should_fail=True)
    sandbox = FakeSandbox("fake-snapshot-fail", config=config)
    await sandbox.create()
    with pytest.raises(SandboxSnapshotFilesystemError):
        await sandbox.snapshot_filesystem("/tmp/dummy_path")

async def test_fake_sandbox_force_checkpoint_while_running():
    """
    Tests that a forced checkpoint succeeds while an execution is running
    and that new executions are prevented.
    """
    config = FakeSandboxConfig(executions=[ExecConfig()])
    sandbox = FakeSandbox("fake-checkpoint-force", config=config)
    await sandbox.create()
    await sandbox.execute(CodeLanguage.PYTHON, "code")

    # Forced checkpoint should succeed
    with tempfile.TemporaryDirectory() as tmpdir:
        await sandbox.checkpoint(tmpdir, force=True)

    # New executions should be blocked
    with pytest.raises(SandboxOperationError, match="Sandbox is shutting down"):
        await sandbox.execute(CodeLanguage.PYTHON, "new code")


async def test_fake_sandbox_token():
    """
    Tests that the FakeSandbox can create and get a sandbox token.
    """
    sandbox = FakeSandbox("fake-token")
    await sandbox.create()
    await sandbox.set_sandbox_token("test-token")
    token = await sandbox.get_sandbox_token()
    assert token == "test-token"
