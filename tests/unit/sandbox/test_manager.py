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
from src.sandbox.config import GCSConfig
from src.sandbox.manager import SandboxManager
from src.sandbox.fake import FakeSandbox, FakeSandboxConfig
from src.sandbox.interface import SandboxCreationError, SandboxOperationError, SandboxSnapshotFilesystemError, SandboxRestoreError
from src.sandbox.lock.factory import LockFactory
from src.sandbox.lock.interface import LockContentionError
from unittest.mock import patch, ANY, MagicMock, AsyncMock
import asyncio
import os
import json
import time

pytestmark = pytest.mark.asyncio

@patch('src.sandbox.factory.create_sandbox_instance')
async def test_manager_create_and_get_sandbox(mock_create_instance):
    """
    Tests that the manager can create, initialize, and retrieve a sandbox.
    """
    # Arrange: Create a real FakeSandbox and configure the factory mock to return it.
    sandbox_to_return = FakeSandbox("test-123")
    mock_create_instance.return_value = sandbox_to_return
    
    mgr = SandboxManager()
    
    # Act
    sandbox = await mgr.create_sandbox(sandbox_id="test-123")
    
    # Assert
    mock_create_instance.assert_called_once_with("test-123", config=ANY)
    assert mgr.get_sandbox("test-123") is sandbox_to_return

@patch('src.sandbox.factory.create_sandbox_instance')
async def test_manager_delete_sandbox(mock_create_instance):
    """Tests that the manager can delete a sandbox."""
    # Arrange
    sandbox = FakeSandbox("test-123")
    mock_create_instance.return_value = sandbox
    mgr = SandboxManager()
    
    delete_event = asyncio.Event()
    await mgr.create_sandbox(
        sandbox_id="test-123",
        delete_callback=lambda sid: delete_event.set()
    )
    
    # Act
    assert mgr.get_sandbox("test-123") is not None
    await mgr.delete_sandbox("test-123")
    
    # Assert
    # Wait for the callback to be fired
    await asyncio.wait_for(delete_event.wait(), timeout=1)
    assert mgr.get_sandbox("test-123") is None

@patch('src.sandbox.factory.create_sandbox_instance')
async def test_manager_create_sandbox_failure(mock_create_instance):
    """
    Tests that the manager handles a sandbox creation failure correctly.
    """
    # Arrange: Configure a real FakeSandbox to fail on create.
    config = FakeSandboxConfig(create_should_fail=True)
    sandbox_that_will_fail = FakeSandbox("test-fail", config=config)
    mock_create_instance.return_value = sandbox_that_will_fail
    
    mgr = SandboxManager()

    # Act & Assert
    with pytest.raises(SandboxCreationError):
        await mgr.create_sandbox(sandbox_id="test-fail")
    
    # Ensure the failed sandbox was not added to the manager
    assert mgr.get_sandbox("test-fail") is None

@patch('src.sandbox.factory.create_sandbox_instance')
async def test_idle_cleanup(mock_create_instance):
    """
    Tests that an idle sandbox is automatically deleted.
    """
    # Arrange
    sandbox = FakeSandbox("idle-sandbox")
    mock_create_instance.return_value = sandbox
    mgr = SandboxManager()
    mgr.enable_idle_cleanup(cleanup_interval=0.1)
    delete_event = asyncio.Event()
    
    # Act
    await mgr.create_sandbox(
        sandbox_id="idle-sandbox",
        idle_timeout=0.1,
        delete_callback=lambda sid: delete_event.set()
    )
    
    # Assert
    assert "idle-sandbox" in mgr._sandboxes
    
    # Wait for the idle cleanup to trigger the deletion and the callback
    await asyncio.wait_for(delete_event.wait(), timeout=1)
    
    assert mgr.get_sandbox("idle-sandbox") is None


@patch('src.sandbox.factory.create_sandbox_instance')
async def test_idle_auto_checkpoint(mock_create_instance, tmp_path):
    """
    Tests that an idle sandbox is automatically checkpointed.
    """
    # Arrange
    sandbox = FakeSandbox("idle-checkpoint-sandbox")
    mock_create_instance.return_value = sandbox
    gcs_config = GCSConfig(
        metadata_mount_path=str(tmp_path),
        metadata_bucket="test-bucket",
        sandbox_checkpoint_mount_path=str(tmp_path),
        sandbox_checkpoint_bucket="test-bucket"
    )
    mgr = SandboxManager(gcs_config=gcs_config)
    mgr.enable_idle_cleanup(cleanup_interval=0.1)
    
    # Spy on checkpoint_sandbox
    mgr.checkpoint_sandbox = AsyncMock(wraps=mgr.checkpoint_sandbox)

    # Act
    await mgr.create_sandbox(
        sandbox_id="idle-checkpoint-sandbox",
        idle_timeout=0.1,
        enable_checkpoint=True,
        enable_idle_timeout_auto_checkpoint=True,
    )
    
    # Assert
    assert "idle-checkpoint-sandbox" in mgr._sandboxes
    
    # Wait for the idle cleanup to trigger the checkpoint
    await asyncio.sleep(0.3)
    
    mgr.checkpoint_sandbox.assert_awaited_once_with("idle-checkpoint-sandbox")

@patch('src.sandbox.factory.create_sandbox_instance')
async def test_reset_idle_timer(mock_create_instance):
    """
    Tests that resetting the idle timer prevents cleanup.
    """
    # Arrange
    sandbox = FakeSandbox("active-sandbox")
    mock_create_instance.return_value = sandbox
    mgr = SandboxManager()
    mgr.enable_idle_cleanup(cleanup_interval=0.1)
    delete_event = asyncio.Event()

    # Act
    await mgr.create_sandbox(
        sandbox_id="active-sandbox",
        idle_timeout=0.2,
        delete_callback=lambda sid: delete_event.set()
    )
    
    # Reset timer and ensure the sandbox is not deleted prematurely
    mgr.reset_idle_timer("active-sandbox")
    await asyncio.sleep(0.1)
    assert not delete_event.is_set()
    assert mgr.get_sandbox("active-sandbox") is not None

    # Wait for the new timeout to fire
    await asyncio.wait_for(delete_event.wait(), timeout=1)
    assert mgr.get_sandbox("active-sandbox") is None

@patch('src.sandbox.factory.create_sandbox_instance')
async def test_delete_all_sandboxes(mock_create_instance):
    """Tests that all sandboxes are deleted."""
    # Arrange
    sandbox1 = FakeSandbox("sandbox1")
    sandbox2 = FakeSandbox("sandbox2")
    mock_create_instance.side_effect = [sandbox1, sandbox2]
    mgr = SandboxManager()

    delete_event1 = asyncio.Event()
    delete_event2 = asyncio.Event()

    await mgr.create_sandbox(
        sandbox_id="sandbox1",
        delete_callback=lambda sid: delete_event1.set()
    )
    await mgr.create_sandbox(
        sandbox_id="sandbox2",
        delete_callback=lambda sid: delete_event2.set()
    )

    # Act
    await mgr.delete_all_sandboxes()
    await asyncio.gather(delete_event1.wait(), delete_event2.wait())

    # Assert
    assert mgr.get_sandbox("sandbox1") is None
    assert mgr.get_sandbox("sandbox2") is None

# --- Checkpoint and Restore Tests ---

@patch('src.sandbox.factory.create_sandbox_instance')
async def test_create_with_checkpoint_enabled(mock_create_instance, tmp_path):
    """
    Tests that creating a sandbox with checkpointing enabled creates the
    necessary directory and metadata file.
    """
    # Arrange
    sandbox = FakeSandbox("checkpoint-sandbox")
    mock_create_instance.return_value = sandbox
    gcs_config = GCSConfig(
        metadata_mount_path=str(tmp_path),
        metadata_bucket="test-bucket",
        sandbox_checkpoint_mount_path=str(tmp_path),
        sandbox_checkpoint_bucket="test-bucket"
    )
    mgr = SandboxManager(gcs_config=gcs_config)
    
    # Act
    await mgr.create_sandbox(sandbox_id="checkpoint-sandbox", enable_checkpoint=True, idle_timeout=300)
    
    # Assert
    _, kwargs = mock_create_instance.call_args
    config = kwargs["config"]
    assert config.checkpointable is True

    sandbox_dir = tmp_path / "sandboxes" / "checkpoint-sandbox"
    metadata_path = sandbox_dir / "metadata.json"
    assert metadata_path.is_file()
    with open(metadata_path, "r") as f:
        metadata = json.load(f)
    assert metadata["idle_timeout"] == 300
    assert metadata["enable_sandbox_checkpoint"] is True

@patch('src.sandbox.factory.create_sandbox_instance')
async def test_create_with_checkpoint_fails_if_not_configured(mock_create_instance):
    """
    Tests that creating a sandbox with checkpointing enabled fails if the
    manager is not configured with a persistence path.
    """
    mgr = SandboxManager() # No gcs_config
    with pytest.raises(SandboxCreationError, match="Checkpointing is not enabled on the server."):
        await mgr.create_sandbox(sandbox_id="test", enable_checkpoint=True)

@patch('google.cloud.storage.Client')
@patch('src.sandbox.handle.CheckpointVerifier')
@patch('src.sandbox.factory.create_sandbox_instance')
async def test_checkpoint_sandbox(mock_create_instance, mock_verifier_class, mock_storage_client, tmp_path):
    """
    Tests that checkpointing a sandbox calls the instance's checkpoint method,
    updates the metadata, and deletes the local instance.
    """
    # Arrange
    mock_verifier_instance = mock_verifier_class.return_value
    mock_verifier_instance.verify = AsyncMock()

    sandbox = FakeSandbox("checkpoint-sandbox")
    mock_create_instance.return_value = sandbox
    gcs_config = GCSConfig(
        metadata_mount_path=str(tmp_path),
        metadata_bucket="test-bucket",
        sandbox_checkpoint_mount_path=str(tmp_path),
        sandbox_checkpoint_bucket="test-bucket"
    )
    mock_lock_factory = MagicMock(spec=LockFactory)
    mock_lock_factory.create_lock.return_value = AsyncMock()
    mgr = SandboxManager(gcs_config=gcs_config, lock_factory=mock_lock_factory)
    await mgr.create_sandbox(
        sandbox_id="checkpoint-sandbox",
        enable_checkpoint=True,
        enable_sandbox_handoff=True,
        idle_timeout=300
    )
    
    # Act
    await mgr.checkpoint_sandbox("checkpoint-sandbox")
    
    # Assert
    mock_verifier_class.assert_called_once()
    mock_verifier_instance.verify.assert_awaited_once()
    
    metadata_path = tmp_path / "sandboxes" / "checkpoint-sandbox" / "metadata.json"
    with open(metadata_path, "r") as f:
        metadata = json.load(f)
    
    assert "latest_sandbox_checkpoint" in metadata
    assert metadata["latest_sandbox_checkpoint"]["bucket"] == "test-bucket"
    assert "sandbox_checkpoints/ckpt_" in metadata["latest_sandbox_checkpoint"]["path"]
    assert mgr.get_sandbox("checkpoint-sandbox") is None # Should be removed from memory

@patch('src.sandbox.factory.create_sandbox_instance')
async def test_restore_sandbox(mock_create_instance, tmp_path):
    """
    Tests that restore_sandbox restores a sandbox from a checkpoint if it's not
    in memory.
    """
    # Arrange
    sandbox_id = "restore-sandbox"
    checkpoints_dir = tmp_path / "sandbox_checkpoints" / "ckpt_123"
    os.makedirs(checkpoints_dir)
    
    metadata_dir = tmp_path / "sandboxes" / sandbox_id
    os.makedirs(metadata_dir)
    metadata_path = metadata_dir / "metadata.json"
    with open(metadata_path, "w") as f:
        json.dump({
            "sandbox_id": sandbox_id,
            "created_timestamp": time.time(),
            "idle_timeout": 180,
            "enable_sandbox_checkpoint": True,
            "latest_sandbox_checkpoint": {
                "bucket": "test-bucket",
                "path": "sandbox_checkpoints/ckpt_123"
            }
        }, f)

    restored_sandbox = FakeSandbox(sandbox_id)
    mock_create_instance.return_value = restored_sandbox
    
    gcs_config = GCSConfig(
        metadata_mount_path=str(tmp_path),
        metadata_bucket="test-bucket",
        sandbox_checkpoint_mount_path=str(tmp_path),
        sandbox_checkpoint_bucket="test-bucket"
    )
    mock_lock_factory = MagicMock(spec=LockFactory)
    mock_lock_factory.create_lock.return_value = AsyncMock()
    mgr = SandboxManager(gcs_config=gcs_config, lock_factory=mock_lock_factory)
    
    # Act
    sandbox = await mgr.restore_sandbox(sandbox_id)
    
    # Assert
    assert sandbox is restored_sandbox
    assert mgr.get_sandbox(sandbox_id) is restored_sandbox # Should be in memory now
    assert mgr._sandboxes[sandbox_id].idle_timeout == 180 # Check that idle_timeout was restored
    
    _, kwargs = mock_create_instance.call_args
    config = kwargs["config"]
    assert config.checkpointable is True


@patch('src.sandbox.factory.create_sandbox_instance')
async def test_checkpoint_sandbox_fails(mock_create_instance, tmp_path):
    """
    Tests that the manager correctly handles a failure during checkpointing.
    """
    # Arrange
    config = FakeSandboxConfig(checkpoint_should_fail=True)
    sandbox = FakeSandbox("fail-sandbox", config=config)
    mock_create_instance.return_value = sandbox
    gcs_config = GCSConfig(
        metadata_mount_path=str(tmp_path),
        metadata_bucket="test-bucket",
        sandbox_checkpoint_mount_path=str(tmp_path),
        sandbox_checkpoint_bucket="test-bucket"
    )
    mgr = SandboxManager(gcs_config=gcs_config)
    await mgr.create_sandbox(sandbox_id="fail-sandbox", enable_checkpoint=True)
    
    # Act & Assert
    with pytest.raises(SandboxOperationError, match="Fake sandbox failed to checkpoint as configured."):
        await mgr.checkpoint_sandbox("fail-sandbox")
    
    # Ensure the sandbox was deleted from memory even on failure
    assert mgr.get_sandbox("fail-sandbox") is None

@patch('src.sandbox.factory.create_sandbox_instance')
async def test_restore_sandbox_fails(mock_create_instance, tmp_path):
    """
    Tests that the manager handles a failure during restore.
    """
    # Arrange
    sandbox_id = "fail-restore"
    checkpoints_dir = tmp_path / "sandbox_checkpoints" / "ckpt_123"
    os.makedirs(checkpoints_dir)
    
    metadata_dir = tmp_path / "sandboxes" / sandbox_id
    os.makedirs(metadata_dir)
    metadata_path = metadata_dir / "metadata.json"
    with open(metadata_path, "w") as f:
        json.dump({
            "sandbox_id": sandbox_id,
            "created_timestamp": time.time(),
            "idle_timeout": 180,
            "enable_sandbox_checkpoint": True,
            "latest_sandbox_checkpoint": {
                "bucket": "test-bucket",
                "path": "sandbox_checkpoints/ckpt_123"
            }
        }, f)

    config = FakeSandboxConfig(restore_should_fail=True)
    sandbox_that_will_fail = FakeSandbox(sandbox_id, config=config)
    mock_create_instance.return_value = sandbox_that_will_fail
    
    gcs_config = GCSConfig(
        metadata_mount_path=str(tmp_path),
        metadata_bucket="test-bucket",
        sandbox_checkpoint_mount_path=str(tmp_path),
        sandbox_checkpoint_bucket="test-bucket"
    )
    mock_lock_factory = MagicMock(spec=LockFactory)
    mock_lock_factory.create_lock.return_value = AsyncMock()
    mgr = SandboxManager(gcs_config=gcs_config, lock_factory=mock_lock_factory)
    
    # Act
    with pytest.raises(SandboxRestoreError, match="Failed to restore sandbox fail-restore"):
        await mgr.restore_sandbox(sandbox_id)
    
    # Assert
    assert mgr.get_sandbox(sandbox_id) is None # Should not be in memory

@patch('src.sandbox.factory.create_sandbox_instance')
async def test_restore_sandbox_starts_idle_timer(mock_create_instance, tmp_path):
    """
    Tests that a restored sandbox with an idle_timeout has its cleanup
    task started.
    """
    # Arrange
    sandbox_id = "restore-timer-sandbox"
    checkpoints_dir = tmp_path / "sandbox_checkpoints" / "ckpt_123"
    os.makedirs(checkpoints_dir)
    
    metadata_dir = tmp_path / "sandboxes" / sandbox_id
    os.makedirs(metadata_dir)
    metadata_path = metadata_dir / "metadata.json"
    with open(metadata_path, "w") as f:
        json.dump({
            "sandbox_id": sandbox_id,
            "created_timestamp": time.time(),
            "idle_timeout": 0.1,
            "enable_sandbox_checkpoint": True,
            "latest_sandbox_checkpoint": {
                "bucket": "test-bucket",
                "path": "sandbox_checkpoints/ckpt_123"
            }
        }, f)

    restored_sandbox = FakeSandbox(sandbox_id)
    mock_create_instance.return_value = restored_sandbox
    
    gcs_config = GCSConfig(
        metadata_mount_path=str(tmp_path),
        metadata_bucket="test-bucket",
        sandbox_checkpoint_mount_path=str(tmp_path),
        sandbox_checkpoint_bucket="test-bucket"
    )
    mock_lock_factory = MagicMock(spec=LockFactory)
    mock_lock_factory.create_lock.return_value = AsyncMock()
    mgr = SandboxManager(gcs_config=gcs_config, lock_factory=mock_lock_factory)
    mgr.enable_idle_cleanup(cleanup_interval=0.1)
    delete_event = asyncio.Event()
    
    # Act
    sandbox = await mgr.restore_sandbox(sandbox_id, delete_callback=lambda sid: delete_event.set())
    assert sandbox is not None
    
    # Assert
    await asyncio.wait_for(delete_event.wait(), timeout=1)
    assert mgr.get_sandbox(sandbox_id) is None
@patch('google.cloud.storage.Client')
@patch('src.sandbox.handle.CheckpointVerifier')
@patch('src.sandbox.factory.create_sandbox_instance')
async def test_checkpoint_restore_checkpoint_restore(mock_create_instance, mock_verifier_class, mock_storage_client, tmp_path):
    """
    Tests the full lifecycle of checkpoint -> restore -> checkpoint -> restore
    to ensure multiple checkpoints are handled correctly.
    """
    # Arrange
    mock_verifier_instance = mock_verifier_class.return_value
    mock_verifier_instance.verify = AsyncMock()

    sandbox_id = "multi-checkpoint-sandbox"
    gcs_config = GCSConfig(
        metadata_mount_path=str(tmp_path),
        metadata_bucket="test-bucket",
        sandbox_checkpoint_mount_path=str(tmp_path),
        sandbox_checkpoint_bucket="test-bucket"
    )
    mock_lock_factory = MagicMock(spec=LockFactory)
    mock_lock_factory.create_lock.return_value = AsyncMock()
    mgr = SandboxManager(gcs_config=gcs_config, lock_factory=mock_lock_factory)

    # --- Create and First Checkpoint ---
    mock_create_instance.return_value = FakeSandbox(sandbox_id)
    await mgr.create_sandbox(sandbox_id=sandbox_id, enable_checkpoint=True)
    await mgr.checkpoint_sandbox(sandbox_id)
    
    metadata_path = tmp_path / "sandboxes" / sandbox_id / "metadata.json"
    assert metadata_path.is_file()
    with open(metadata_path, "r") as f:
        metadata = json.load(f)
    first_checkpoint_path = metadata["latest_sandbox_checkpoint"]["path"]
    assert "sandbox_checkpoints/ckpt_" in first_checkpoint_path
    assert mgr.get_sandbox(sandbox_id) is None

    # --- First Restore ---
    restored_sandbox_1 = FakeSandbox(sandbox_id)
    mock_create_instance.return_value = restored_sandbox_1
    sandbox_1 = await mgr.restore_sandbox(sandbox_id)
    assert sandbox_1 is restored_sandbox_1
    assert mgr.get_sandbox(sandbox_id) is restored_sandbox_1

    # --- Second Checkpoint ---
    await mgr.checkpoint_sandbox(sandbox_id)
    assert metadata_path.is_file()
    with open(metadata_path, "r") as f:
        metadata = json.load(f)
    second_checkpoint_path = metadata["latest_sandbox_checkpoint"]["path"]
    assert "sandbox_checkpoints/ckpt_" in second_checkpoint_path
    assert second_checkpoint_path != first_checkpoint_path
    assert mgr.get_sandbox(sandbox_id) is None

    # --- Second Restore ---
    restored_sandbox_2 = FakeSandbox(sandbox_id)
    mock_create_instance.return_value = restored_sandbox_2
    sandbox_2 = await mgr.restore_sandbox(sandbox_id)
    assert sandbox_2 is restored_sandbox_2
    assert mgr.get_sandbox(sandbox_id) is restored_sandbox_2

# --- Filesystem Snapshot Tests ---

@patch('src.sandbox.factory.create_sandbox_instance')
async def test_create_with_filesystem_snapshot(mock_create_instance, tmp_path):
    """
    Tests that creating a sandbox with a filesystem snapshot sets the correct config.
    """
    # Arrange
    sandbox = FakeSandbox("snapshot-sandbox")
    mock_create_instance.return_value = sandbox
    gcs_config = GCSConfig(filesystem_snapshot_mount_path=str(tmp_path), filesystem_snapshot_bucket="test-bucket")
    mgr = SandboxManager(gcs_config=gcs_config)
    
    # Act
    await mgr.create_sandbox(sandbox_id="snapshot-sandbox", filesystem_snapshot_name="my-snapshot")
    
    # Assert
    _, kwargs = mock_create_instance.call_args
    config = kwargs["config"]
    assert config.filesystem_snapshot_path == str(tmp_path / "filesystem_snapshots" / "my-snapshot" / "my-snapshot.tar")

@patch('src.sandbox.factory.create_sandbox_instance')
async def test_create_with_filesystem_snapshot_fails_if_not_configured(mock_create_instance):
    """
    Tests that creating a sandbox with a filesystem snapshot fails if the manager
    is not configured with a snapshot path.
    """
    mgr = SandboxManager() # No gcs_config
    with pytest.raises(SandboxCreationError, match="Filesystem snapshot is not enabled on the server."):
        await mgr.create_sandbox(sandbox_id="test", filesystem_snapshot_name="my-snapshot")

@patch('src.sandbox.factory.create_sandbox_instance')
async def test_snapshot_filesystem(mock_create_instance, tmp_path):
    """
    Tests that snapshotting a sandbox's filesystem calls the instance's method.
    """
    # Arrange
    sandbox = FakeSandbox("snapshot-sandbox")
    mock_create_instance.return_value = sandbox
    gcs_config = GCSConfig(filesystem_snapshot_mount_path=str(tmp_path), filesystem_snapshot_bucket="test-bucket")
    mgr = SandboxManager(gcs_config=gcs_config)
    await mgr.create_sandbox(sandbox_id="snapshot-sandbox")
    
    # Act
    await mgr.snapshot_filesystem("snapshot-sandbox", "my-snapshot")
        
    # Assert
    snapshot_path = tmp_path / "filesystem_snapshots" / "my-snapshot" / "my-snapshot.tar"
    assert snapshot_path.is_file()

@patch('src.sandbox.factory.create_sandbox_instance')
async def test_snapshot_filesystem_fails(mock_create_instance, tmp_path):
    """
    Tests that the manager correctly handles a failure during snapshotting.
    """
    # Arrange
    config = FakeSandboxConfig(snapshot_filesystem_should_fail=True)
    sandbox = FakeSandbox("fail-snapshot-sandbox", config=config)
    mock_create_instance.return_value = sandbox
    gcs_config = GCSConfig(filesystem_snapshot_mount_path=str(tmp_path), filesystem_snapshot_bucket="test-bucket")
    mgr = SandboxManager(gcs_config=gcs_config)
    await mgr.create_sandbox(sandbox_id="fail-snapshot-sandbox")
    
    # Act & Assert
    with pytest.raises(SandboxSnapshotFilesystemError, match="Fake sandbox failed to snapshot filesystem as configured."):
        await mgr.snapshot_filesystem("fail-snapshot-sandbox", "my-snapshot")


# --- Locking and Handoff Tests ---

class TestManagerLocking:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.tmp_path = tmp_path
        self.mock_lock_factory = MagicMock(spec=LockFactory)
        gcs_config = GCSConfig(
            metadata_mount_path=str(tmp_path),
            metadata_bucket="test-bucket",
            sandbox_checkpoint_mount_path=str(tmp_path),
            sandbox_checkpoint_bucket="test-bucket"
        )
        self.mgr = SandboxManager(gcs_config=gcs_config, lock_factory=self.mock_lock_factory)

    @patch('src.sandbox.factory.create_sandbox_instance')
    async def test_create_with_handoff_acquires_lock(self, mock_create_instance):
        # Arrange
        mock_lock = AsyncMock()
        self.mock_lock_factory.create_lock.return_value = mock_lock
        mock_create_instance.return_value = FakeSandbox("handoff-sandbox")

        # Act
        await self.mgr.create_sandbox(
            sandbox_id="handoff-sandbox",
            enable_checkpoint=True,
            enable_sandbox_handoff=True
        )

        # Assert
        self.mock_lock_factory.create_lock.assert_called_once_with(
            "handoff-sandbox",
            "sandboxes/handoff-sandbox/lock.json",
            on_release_requested=ANY,
            on_renewal_error=ANY,
        )
        mock_lock.acquire.assert_awaited_once()
        handle = self.mgr._sandboxes.get("handoff-sandbox")
        assert handle is not None
        assert handle.lock is mock_lock

    @patch('src.sandbox.factory.create_sandbox_instance')
    async def test_delete_sandbox_releases_lock(self, mock_create_instance):
        # Arrange
        mock_lock = AsyncMock()
        self.mock_lock_factory.create_lock.return_value = mock_lock
        mock_create_instance.return_value = FakeSandbox("locked-sandbox")
        await self.mgr.create_sandbox(
            sandbox_id="locked-sandbox",
            enable_checkpoint=True,
            enable_sandbox_handoff=True
        )
        # Act
        await self.mgr.delete_sandbox("locked-sandbox")

        # Assert
        mock_lock.release.assert_awaited_once()

    @patch('src.sandbox.factory.create_sandbox_instance')
    async def test_create_sandbox_releases_resources_on_lock_failure(self, mock_create_instance):
        # Arrange
        mock_lock = AsyncMock()
        mock_lock.acquire.side_effect = LockContentionError("Failed to acquire lock")
        self.mock_lock_factory.create_lock.return_value = mock_lock
        mock_create_instance.return_value = FakeSandbox("lock-fail-sandbox")
        
        initial_ip_pool_size = len(self.mgr._ip_pool)

        # Act & Assert
        with pytest.raises(SandboxCreationError):
            await self.mgr.create_sandbox(
                sandbox_id="lock-fail-sandbox",
                enable_checkpoint=True,
                enable_sandbox_handoff=True
            )
        
        assert self.mgr.get_sandbox("lock-fail-sandbox") is None
        assert len(self.mgr._ip_pool) == initial_ip_pool_size
        # The release method should be called to clean up resources even if acquisition fails.
        mock_lock.release.assert_awaited_once()

    @patch('src.sandbox.factory.create_sandbox_instance')
    async def test_restore_sandbox_with_handoff_acquires_lock(self, mock_create_instance):
        # Arrange
        sandbox_id = "restore-handoff-sandbox"
        # 1. Create metadata that indicates handoff is enabled
        metadata_dir = self.tmp_path / "sandboxes" / sandbox_id
        os.makedirs(metadata_dir)
        metadata_path = metadata_dir / "metadata.json"
        with open(metadata_path, "w") as f:
            json.dump({
                "sandbox_id": sandbox_id,
                "created_timestamp": time.time(),
                "idle_timeout": 180,
                "enable_sandbox_checkpoint": True,
                "enable_sandbox_handoff": True, # IMPORTANT
                "latest_sandbox_checkpoint": {
                    "bucket": "test-bucket",
                    "path": "sandbox_checkpoints/ckpt_123"
                }
            }, f)
        
        checkpoints_dir = self.tmp_path / "sandbox_checkpoints" / "ckpt_123"
        os.makedirs(checkpoints_dir)

        mock_lock = AsyncMock()
        self.mock_lock_factory.create_lock.return_value = mock_lock
        mock_create_instance.return_value = FakeSandbox(sandbox_id)

        # Act
        await self.mgr.restore_sandbox(sandbox_id)

        # Assert
        self.mock_lock_factory.create_lock.assert_called_once_with(
            sandbox_id,
            f"sandboxes/{sandbox_id}/lock.json",
            on_release_requested=ANY,
            on_renewal_error=ANY,
        )
        mock_lock.acquire.assert_awaited_once()
        handle = self.mgr._sandboxes.get(sandbox_id)
        assert handle is not None
        assert handle.lock is mock_lock

    @patch('src.sandbox.factory.create_sandbox_instance')
    async def test_restore_sandbox_releases_resources_on_failure(self, mock_create_instance):
        # Arrange
        sandbox_id = "restore-fail-sandbox"
        # 1. Create metadata
        metadata_dir = self.tmp_path / "sandboxes" / sandbox_id
        os.makedirs(metadata_dir)
        metadata_path = metadata_dir / "metadata.json"
        with open(metadata_path, "w") as f:
            json.dump({
                "sandbox_id": sandbox_id,
                "created_timestamp": time.time(),
                "idle_timeout": 180,
                "enable_sandbox_checkpoint": True,
                "enable_sandbox_handoff": True,
                "latest_sandbox_checkpoint": {
                    "bucket": "test-bucket",
                    "path": "sandbox_checkpoints/ckpt_123"
                }
            }, f)
        checkpoints_dir = self.tmp_path / "sandbox_checkpoints" / "ckpt_123"
        os.makedirs(checkpoints_dir)

        # 2. Configure mocks
        mock_lock = AsyncMock()
        self.mock_lock_factory.create_lock.return_value = mock_lock
        
        config = FakeSandboxConfig(restore_should_fail=True)
        sandbox_that_will_fail = FakeSandbox(sandbox_id, config=config)
        mock_create_instance.return_value = sandbox_that_will_fail
        
        initial_ip_pool_size = len(self.mgr._ip_pool)

        # Act & Assert
        with pytest.raises(SandboxRestoreError):
            await self.mgr.restore_sandbox(sandbox_id)
        
        assert self.mgr.get_sandbox(sandbox_id) is None
        assert len(self.mgr._ip_pool) == initial_ip_pool_size
        mock_lock.release.assert_awaited_once()

    @patch('google.cloud.storage.Client')
    @patch('src.sandbox.handle.CheckpointVerifier')
    @patch('src.sandbox.factory.create_sandbox_instance')
    async def test_on_release_requested_checkpoints_sandbox(self, mock_create_instance, mock_verifier_class, mock_storage_client):
        # Arrange
        mock_verifier_instance = mock_verifier_class.return_value
        mock_verifier_instance.verify = AsyncMock()

        sandbox_id = "release-req-sandbox"
        mock_lock = AsyncMock()
        
        # Capture the callback
        on_release_requested_callback = None
        def capture_callback(*args, **kwargs):
            nonlocal on_release_requested_callback
            on_release_requested_callback = kwargs.get("on_release_requested")
            return mock_lock
        self.mock_lock_factory.create_lock.side_effect = capture_callback
        
        mock_create_instance.return_value = FakeSandbox(sandbox_id)
        
        # Spy on the checkpoint method
        self.mgr.checkpoint_sandbox = AsyncMock(wraps=self.mgr.checkpoint_sandbox)

        await self.mgr.create_sandbox(
            sandbox_id=sandbox_id,
            enable_checkpoint=True,
            enable_sandbox_handoff=True
        )
        assert on_release_requested_callback is not None

        # Act
        await on_release_requested_callback()

        # Assert
        self.mgr.checkpoint_sandbox.assert_awaited_once_with(sandbox_id, force=True)

    @patch('src.sandbox.factory.create_sandbox_instance')
    async def test_on_renewal_error_deletes_sandbox(self, mock_create_instance):
        # Arrange
        sandbox_id = "renewal-err-sandbox"
        mock_lock = AsyncMock()
        
        # Capture the callback
        on_renewal_error_callback = None
        def capture_callback(*args, **kwargs):
            nonlocal on_renewal_error_callback
            on_renewal_error_callback = kwargs.get("on_renewal_error")
            return mock_lock
        self.mock_lock_factory.create_lock.side_effect = capture_callback
        
        mock_create_instance.return_value = FakeSandbox(sandbox_id)
        
        # Spy on the delete method
        self.mgr.delete_sandbox = AsyncMock(wraps=self.mgr.delete_sandbox)

        await self.mgr.create_sandbox(
            sandbox_id=sandbox_id,
            enable_checkpoint=True,
            enable_sandbox_handoff=True
        )
        assert on_renewal_error_callback is not None

        # Act
        await on_renewal_error_callback()

        # Assert
        self.mgr.delete_sandbox.assert_awaited_once_with(sandbox_id)

    @patch('src.sandbox.factory.create_sandbox_instance')
    async def test_kill_sandbox(self, mock_create_instance):
        # Arrange
        mock_lock = AsyncMock()
        self.mock_lock_factory.create_lock.return_value = mock_lock
        mock_create_instance.return_value = FakeSandbox("kill-sandbox")
        self.mgr.delete_sandbox = AsyncMock(wraps=self.mgr.delete_sandbox)

        await self.mgr.create_sandbox(
            sandbox_id="kill-sandbox",
            enable_checkpoint=True,
            enable_sandbox_handoff=True
        )

        # Act
        await self.mgr.kill_sandbox("kill-sandbox")

        # Assert
        self.mgr.delete_sandbox.assert_awaited_once_with("kill-sandbox", delete_metadata=True)

    @patch('src.sandbox.factory.create_sandbox_instance')
    async def test_delete_sandbox_deletes_metadata(self, mock_create_instance):
        # Arrange
        mock_lock = AsyncMock()
        self.mock_lock_factory.create_lock.return_value = mock_lock
        mock_create_instance.return_value = FakeSandbox("delete-meta-sandbox")

        await self.mgr.create_sandbox(
            sandbox_id="delete-meta-sandbox",
            enable_checkpoint=True,
            enable_sandbox_handoff=True
        )
        
        handle = self.mgr._sandboxes.get("delete-meta-sandbox")
        handle.delete_metadata = AsyncMock()

        # Act
        await self.mgr.delete_sandbox("delete-meta-sandbox", delete_metadata=True)

        # Assert
        handle.delete_metadata.assert_awaited_once()


@patch('src.sandbox.handle.SandboxHandle.verify_checkpoint_persisted', new_callable=AsyncMock)
@patch('src.sandbox.factory.create_sandbox_instance')
async def test_checkpoint_sandbox_verifies_persistence(mock_create_instance, mock_verify, tmp_path):
    """
    Tests that checkpoint_sandbox calls the handle's verification method.
    """
    # Arrange
    sandbox = FakeSandbox("verify-sandbox")
    mock_create_instance.return_value = sandbox
    gcs_config = GCSConfig(
        metadata_mount_path=str(tmp_path),
        metadata_bucket="test-bucket",
        sandbox_checkpoint_mount_path=str(tmp_path),
        sandbox_checkpoint_bucket="test-bucket"
    )
    mock_lock_factory = MagicMock(spec=LockFactory)
    mock_lock_factory.create_lock.return_value = AsyncMock()
    mgr = SandboxManager(gcs_config=gcs_config, lock_factory=mock_lock_factory)
    await mgr.create_sandbox(
        sandbox_id="verify-sandbox",
        enable_checkpoint=True,
        enable_sandbox_handoff=True,
    )

    # Act
    await mgr.checkpoint_sandbox("verify-sandbox")

    # Assert
    mock_verify.assert_awaited_once()


@patch('secrets.token_hex', return_value='test_token')
@patch('src.sandbox.factory.create_sandbox_instance')
async def test_manager_create_sandbox_creates_token(mock_create_instance, mock_token_hex):
    """
    Tests that the manager creates a sandbox token when creating a sandbox.
    """
    # Arrange
    sandbox_to_return = FakeSandbox("test-token-sandbox")
    mock_create_instance.return_value = sandbox_to_return
    
    mgr = SandboxManager()
    
    # Act
    sandbox = await mgr.create_sandbox(sandbox_id="test-token-sandbox")
    
    # Assert
    assert sandbox is sandbox_to_return
    token = await sandbox.get_sandbox_token()
    assert token == 'test_token'



